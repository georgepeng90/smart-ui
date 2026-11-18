using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using TMPro;
using UnityEditor;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using ImageComponent = UnityEngine.UI.Image;
using Object = UnityEngine.Object;

namespace SmartUI.UGUI.Editor
{
    internal sealed class BuildResources
    {
        internal const float DesignPixelsPerUnit = 100f;
        readonly BuildRequest request;
        readonly Dictionary<(Target, bool), DeclaredNode[]> declaredNodes = new();
        readonly Dictionary<string, TargetState> controlStates = new();
        readonly Dictionary<string, (TextStyle style, TMP_FontAsset font, Material material)> fonts = new();

        internal BuildResources(BuildRequest request) => this.request = request;

        internal ImageAsset Image(string id) => request.Assets.TryGetValue(id, out var asset) ? asset
            : throw new InvalidOperationException($"No image mapping for '{id}'.");

        internal static string ImagePath(ImageAsset asset) => asset.OutputPath ?? asset.Path;

        internal static string Mode(ImageAsset asset) => (string)asset.Geometry?["mode"] ?? "contain";

        internal static bool Sliced(ImageAsset asset) => Mode(asset) is "nine_slice" or "horizontal" or "vertical";

        internal static Vector4 Borders(ImageAsset asset)
        {
            var borders = asset.Geometry?["borders"];
            return !Sliced(asset) ? Vector4.zero : new Vector4((float)borders["left"], (float)borders["bottom"],
                (float)borders["right"], (float)borders["top"]);
        }

        static Rect ContentRect(ImageAsset asset)
        {
            var content = asset.Geometry?["content_rect"];
            return content == null ? new Rect(0, 0, asset.Width, asset.Height) : new Rect((float)content["x"], (float)content["y"],
                (float)content["width"], (float)content["height"]);
        }

        internal static Rect ImageRect(ImageAsset asset, Rect target)
        {
            var content = ContentRect(asset);
            float x = content.x, y = content.y, width = content.width, height = content.height;
            if (Sliced(asset))
            {
                var border = Borders(asset);
                float slicedWidth = target.width + asset.Width - width;
                float slicedHeight = target.height + asset.Height - height;
                if ((Mode(asset) == "horizontal" && target.height != height) ||
                    (Mode(asset) == "vertical" && target.width != width) ||
                    slicedWidth <= border.x + border.z || slicedHeight <= border.y + border.w)
                    throw new InvalidOperationException($"Image '{asset.Path}' has unsupported size {target.size} for its slice geometry.");
                return new Rect(target.x - x, target.y - y, slicedWidth, slicedHeight);
            }
            float sx = target.width / width, sy = target.height / height;
            if (Mode(asset) == "contain")
                sx = sy = Mathf.Min(sx, sy);
            return new Rect(target.x + (target.width - width * sx) / 2 - x * sx,
                target.y + (target.height - height * sy) / 2 - y * sy, asset.Width * sx, asset.Height * sy);
        }

        internal void RequireSwapGeometry(ImageAsset original, ImageAsset replacement, Rect target)
        {
            if (Sliced(original) != Sliced(replacement) || Borders(original) != Borders(replacement) ||
                ImageRect(original, target) != ImageRect(replacement, target))
                throw new InvalidOperationException($"Image '{replacement.Path}' must match the layout and borders of '{original.Path}' for a Sprite swap.");
        }

        internal void RequireMatchingImageOverride(ImageComponent original, ImageAsset replacement)
        {
            var sourcePath = AssetDatabase.GetAssetPath(original.sprite);
            var source = request.Assets.Values.SingleOrDefault(asset => ImagePath(asset) == sourcePath);
            if (source == null || original.sprite == null || original.type != (Sliced(source) ? ImageComponent.Type.Sliced : ImageComponent.Type.Simple) ||
                Mode(source) != Mode(replacement) || Borders(source) != Borders(replacement) ||
                ContentRect(source) != ContentRect(replacement) || source.Width != replacement.Width || source.Height != replacement.Height)
                throw new InvalidOperationException($"'{original.name}' requires a mapped replacement Sprite with matching geometry and dimensions.");
        }

        internal static Sprite LoadSprite(ImageAsset asset)
        {
            string path = ImagePath(asset);
            var sprites = AssetDatabase.LoadAllAssetsAtPath(path).OfType<Sprite>().ToArray();
            if (sprites.Length != 1)
                throw new InvalidOperationException($"'{path}' expected 1 Sprite from '{asset.Path}', actual {sprites.Length}.");
            var sprite = sprites[0];
            var expectedRect = new Rect(0, 0, asset.Width, asset.Height);
            if (sprite.rect != expectedRect)
                throw new InvalidOperationException($"'{path}' Sprite.rect expected {expectedRect} from PNG '{asset.Path}', actual {sprite.rect}.");
            if (sprite.border != Borders(asset))
                throw new InvalidOperationException($"'{path}' Sprite.border (left,bottom,right,top) expected {Borders(asset)} from '{asset.Path}' geometry.borders, actual {sprite.border}.");
            // The 100 PPU contract applies to generated Sprites and mapped sliced artwork.
            if ((asset.OutputPath != null || Sliced(asset)) && sprite.pixelsPerUnit != DesignPixelsPerUnit)
                throw new InvalidOperationException($"'{path}' Sprite.pixelsPerUnit expected {DesignPixelsPerUnit} from the SmartUI design pixel scale for '{asset.Path}', actual {sprite.pixelsPerUnit}.");
            PrefabAssets.RequireClean(path, sprites[0]);
            return sprites[0];
        }

        internal static void RequireCanvasScale(Canvas canvas, string source)
        {
            void Require(float actual, string property)
            {
                if (actual != DesignPixelsPerUnit)
                    throw new InvalidOperationException($"'{source}' {property} expected {DesignPixelsPerUnit} from the SmartUI design pixel scale, actual {actual}.");
            }
            Require(canvas.referencePixelsPerUnit, "Canvas.referencePixelsPerUnit");
            var scaler = canvas.rootCanvas.GetComponent<CanvasScaler>();
            if (scaler != null && scaler.enabled)
                Require(scaler.referencePixelsPerUnit, "CanvasScaler.referencePixelsPerUnit");
        }

        internal void CheckHostAndImages(GameObject scope, Target target, TargetResult result, JObject baseline = null, string sceneSource = null)
        {
            var canvases = new HashSet<Canvas>();
            void ReadCanvas(Canvas canvas)
            {
                if (canvas == null || !canvases.Add(canvas))
                    return;
                string source = (scope.scene.path == "" ? target.Path : scope.scene.path) + "/" + Hierarchy(canvas.transform);
                result.CanvasReferencePixelsPerUnit[source] = canvas.referencePixelsPerUnit;
                RequireCanvasScale(canvas, source);
            }
            ReadCanvas(scope.GetComponentsInParent<Canvas>().FirstOrDefault(canvas => canvas.isActiveAndEnabled));
            var sourceRoot = sceneSource == null ? null : PrefabUtility.GetCorrespondingObjectFromSourceAtPath(scope, sceneSource);
            foreach (var node in Nodes(target, baseline))
            {
                var rect = scope.transform.Find(node.Path) as RectTransform;
                if (sceneSource == null && node.OwnerPath != target.Path)
                {
                    var owner = AssetDatabase.LoadAssetAtPath<GameObject>(node.OwnerPath);
                    var original = owner.transform.Find(node.OwnerNodePath) as RectTransform;
                    if (rect == null || original == null || rect.anchorMin != original.anchorMin ||
                        rect.anchorMax != original.anchorMax || rect.pivot != original.pivot ||
                        rect.anchoredPosition != original.anchoredPosition || rect.sizeDelta != original.sizeDelta)
                        throw new InvalidOperationException($"'{target.Path}/{node.Path}' RectTransform expected {RectLayout(original)} from '{node.OwnerPath}/{node.OwnerNodePath}', actual {RectLayout(rect)}.");
                }
                if (node.SpritePath == null)
                    continue;
                var image = rect?.GetComponent<ImageComponent>();
                var source = sourceRoot == null ? null : sourceRoot.transform.Find(node.Path)?.GetComponent<ImageComponent>();
                if (image == null)
                {
                    if (sceneSource == null || source != null && source.type == ImageComponent.Type.Sliced)
                        throw new InvalidOperationException($"'{scope.scene.path}/{Hierarchy(scope.transform)}/{node.Path}' expected {node.ImageType} Image from '{sceneSource ?? target.Path}', actual Image missing.");
                    continue;
                }
                ReadCanvas(image.canvas);
                if (source != null && source.type == ImageComponent.Type.Sliced)
                    RequireSceneImage(image, source, sceneSource);
                else if (sceneSource == null)
                {
                    string location = $"'{target.Path}/{node.Path}'";
                    if (image.type != node.ImageType)
                        throw new InvalidOperationException($"{location} Image.type expected {node.ImageType} from '{node.OwnerPath}/{node.OwnerNodePath}', actual {image.type}.");
                    if (image.type == ImageComponent.Type.Sliced && image.pixelsPerUnitMultiplier != 1f)
                        throw new InvalidOperationException($"{location} Image.pixelsPerUnitMultiplier expected 1 from the SmartUI sliced Image design pixel scale, actual {image.pixelsPerUnitMultiplier}.");
                    if (image.sprite == null || AssetDatabase.GetAssetPath(image.sprite) != node.SpritePath)
                        throw new InvalidOperationException($"{location} Image.sprite expected '{node.SpritePath}' from the declared artwork and Sprite overrides, actual {SpriteDescription(image.sprite)}.");
                }
            }
        }

        static string RectLayout(RectTransform rect) => rect == null ? "missing" :
            $"anchors {rect.anchorMin:G9}..{rect.anchorMax:G9}, pivot {rect.pivot:G9}, anchoredPosition {rect.anchoredPosition:G9}, sizeDelta {rect.sizeDelta:G9}";

        static void RequireSceneImage(ImageComponent image, ImageComponent source, string sourcePath)
        {
            // The enclosing Prefab includes its Variant and nested instance Sprite parameters.
            string location = $"'{image.gameObject.scene.path}/{Hierarchy(image.transform)}'";
            string declaration = $"'{sourcePath}/{Hierarchy(source.transform)}'";
            if (image.type != ImageComponent.Type.Sliced)
                throw new InvalidOperationException($"{location} Image.type expected Sliced from {declaration}, actual {image.type}.");
            if (image.pixelsPerUnitMultiplier != 1f)
                throw new InvalidOperationException($"{location} Image.pixelsPerUnitMultiplier expected 1 from {declaration}, actual {image.pixelsPerUnitMultiplier}.");
            if (image.sprite != source.sprite)
                throw new InvalidOperationException($"{location} Image.sprite expected {SpriteDescription(source.sprite)} from {declaration}, actual {SpriteDescription(image.sprite)}.");

            var actual = image.overrideSprite; // uGUI returns the Sprite currently used for drawing.
            if (actual == null || (actual != source.sprite && !IsButtonState(source, actual)))
                throw new InvalidOperationException($"{location} Image active Sprite expected {SpriteDescription(source.sprite)} or a declared SpriteSwap state from {declaration}, actual {SpriteDescription(actual)}.");
            if (actual.pixelsPerUnit != DesignPixelsPerUnit)
                throw new InvalidOperationException($"{location} Sprite.pixelsPerUnit expected {DesignPixelsPerUnit} from {declaration}, actual {SpriteDescription(actual)}.");
        }

        static string SpriteDescription(Sprite sprite) => sprite == null ? "null" :
            $"'{AssetDatabase.GetAssetPath(sprite)}' ({sprite.name}, PPU {sprite.pixelsPerUnit})";

        static bool IsButtonState(ImageComponent source, Sprite sprite)
        {
            foreach (var button in source.GetComponentsInParent<Button>(true))
            {
                if (button.targetGraphic != source || button.transition != Selectable.Transition.SpriteSwap)
                    continue;
                var states = button.spriteState;
                if (sprite == states.highlightedSprite || sprite == states.pressedSprite ||
                    sprite == states.selectedSprite || sprite == states.disabledSprite)
                    return true;
            }
            return false;
        }

        struct DeclaredNode
        {
            internal string Id, Path, OwnerPath, OwnerNodePath, SpritePath;
            internal ImageComponent.Type ImageType;
        }

        DeclaredNode[] Nodes(Target target, JObject baseline)
        {
            var key = (target, baseline != null);
            if (!declaredNodes.TryGetValue(key, out var nodes))
                declaredNodes.Add(key, nodes = ResolveNodes(target, baseline).ToArray());
            return nodes;
        }

        IEnumerable<DeclaredNode> ResolveNodes(Target target, JObject baseline)
        {
            DeclaredNode[] ControlNodes(string controlId)
            {
                var selected = request.Targets.FirstOrDefault(item => item.Kind != "page" && item.Id == controlId);
                if (baseline == null && selected != null)
                    return Nodes(selected, null);
                if (!controlStates.TryGetValue(controlId, out var state))
                {
                    foreach (var kind in new[] { "base", "variant" })
                    {
                        state = BuildFiles.Read<TargetState>(BuildFiles.StatePath(request.DataRoot, kind, controlId));
                        if (state?.ProjectId == request.ProjectId && state.Path == request.Controls[controlId].Path)
                            break;
                        state = null;
                    }
                    controlStates.Add(controlId, state);
                }
                // External controls have no generated declaration.
                return state == null ? Array.Empty<DeclaredNode>() : Nodes(state.Declaration, state.Managed);
            }
            string SpritePath(string id, string field, bool local) => baseline == null ? ImagePath(Image(id)) :
                AssetDatabase.GUIDToAssetPath((string)(local ? baseline[field]["reference"] : baseline[field])[0]);
            if (target.Kind == "variant")
            {
                foreach (var inherited in ControlNodes(target.BaseControlId))
                {
                    var node = inherited;
                    var change = target.Overrides.SingleOrDefault(entry => entry.NodeId == node.Id && entry.Property == "asset_id");
                    if (change != null)
                        node.SpritePath = SpritePath((string)change.Value, node.Id + "/override/m_Sprite", true);
                    yield return node;
                }
                yield break;
            }
            var paths = new Dictionary<string, string>();
            foreach (var node in target.Nodes)
            {
                string path = (node.ParentId == null ? "" : paths[node.ParentId] + "/") + PrefabGraph.NodeName(node.Id);
                paths.Add(node.Id, path);
                var declared = new DeclaredNode { Id = node.Id, Path = path, OwnerPath = target.Path, OwnerNodePath = path };
                if (node.Kind == "image")
                {
                    declared.SpritePath = SpritePath(node.AssetId, node.Id + "/m_Sprite", false);
                    declared.ImageType = baseline != null ? (ImageComponent.Type)(int)baseline[node.Id + "/m_Type"] :
                        Sliced(Image(node.AssetId)) ? ImageComponent.Type.Sliced : ImageComponent.Type.Simple;
                }
                yield return declared;
                if (node.Kind != "control")
                    continue;
                var control = request.Controls[node.ControlId];
                string instance = path + "/" + PrefabGraph.InstanceName(node.Id);
                yield return new DeclaredNode { Path = instance, OwnerPath = target.Path, OwnerNodePath = instance };
                foreach (var child in ControlNodes(node.ControlId))
                {
                    var nested = child;
                    foreach (var pair in node.Parameters)
                    {
                        var parameter = control.Parameters[pair.Key];
                        if (parameter.Property == "asset_id" && control.Slots[parameter.SlotId].NodeId == child.Id)
                            nested.SpritePath = SpritePath((string)pair.Value, node.Id + "/parameter/" + pair.Key, true);
                    }
                    nested.Id = null; // The enclosing control exposes its own nodes as public slots.
                    nested.Path = instance + "/" + child.Path;
                    yield return nested;
                }
            }
        }

        static string Hierarchy(Transform node) => node.parent == null ? node.name : Hierarchy(node.parent) + "/" + node.name;

        internal void CheckLoadedHosts(GameObject asset, Target target, TargetResult result, JObject baseline)
        {
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.isLoaded || UnityEditor.SceneManagement.EditorSceneManager.IsPreviewScene(scene))
                    continue;
                foreach (var sceneRoot in scene.GetRootGameObjects())
                    foreach (var rect in sceneRoot.GetComponentsInChildren<RectTransform>(true))
                        if (PrefabUtility.IsAnyPrefabInstanceRoot(rect.gameObject) &&
                            PrefabUtility.GetCorrespondingObjectFromSourceAtPath(rect.gameObject, target.Path) == asset)
                        {
                            var sourcePath = target.Path;
                            for (var parent = rect.transform; parent != null; parent = parent.parent)
                            {
                                if (!PrefabUtility.IsAnyPrefabInstanceRoot(parent.gameObject))
                                    continue;
                                string path = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(parent.gameObject);
                                if (path.StartsWith("Assets/SmartUI/Controls/", StringComparison.Ordinal) ||
                                    path.StartsWith("Assets/SmartUI/Pages/", StringComparison.Ordinal))
                                    sourcePath = path;
                            }
                            CheckHostAndImages(rect.gameObject, target, result, baseline, sourcePath);
                        }
            }
        }

        internal void ValidateImages(bool verifying)
        {
            foreach (var item in request.Assets)
            {
                var asset = item.Value;
                var source = BuildFiles.RelativePath(asset.Path);
                if (PrefabAssets.FileHash(source) != asset.Sha256 || !File.Exists(source))
                    throw new InvalidOperationException($"PNG source '{asset.Path}' changed or is missing.");
                using (var reader = new BinaryReader(File.OpenRead(source)))
                {
                    var header = reader.ReadBytes(24);
                    int Dimension(int offset) => (header[offset] << 24) | (header[offset + 1] << 16) |
                        (header[offset + 2] << 8) | header[offset + 3];
                    if (header.Length != 24 || !header.Take(8).SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }) ||
                        Dimension(16) != asset.Width || Dimension(20) != asset.Height)
                        throw new InvalidOperationException($"PNG '{asset.Path}' does not have the declared dimensions.");
                }

                var content = ContentRect(asset);
                if (!(Mode(asset) is "stretch" or "contain" or "nine_slice" or "horizontal" or "vertical") ||
                    asset.Width <= 0 || asset.Height <= 0 || asset.Width > 16384 || asset.Height > 16384 ||
                    content.xMin < 0 || content.yMin < 0 || content.xMax > asset.Width || content.yMax > asset.Height ||
                    content.width <= 0 || content.height <= 0)
                    throw new InvalidOperationException($"Image '{asset.Path}' has unsupported geometry '{Mode(asset)}'.");
                var path = ImagePath(asset);
                PrefabAssets.RequireAssetPath(path);
                if (asset.OutputPath == null)
                {
                    LoadSprite(asset);
                    continue;
                }
                if (!path.EndsWith(".png", StringComparison.Ordinal) ||
                    string.Equals(path, asset.Path, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException($"'{path}' is not a separate generated Sprite output path.");
                BuildFiles.RequireOutputPath("Sprites", path);

                var files = BuildFiles.Snapshot(path);
                BuildFiles.RequireSnapshot(path, files, asset.Expected);
                var statePath = BuildFiles.StatePath(request.DataRoot, "sprites", item.Key);
                BuildFiles.RequireNoPending(statePath);
                var state = BuildFiles.Read<SpriteState>(statePath);
                if (state == null ? files.Sha256 != null || files.MetaSha256 != null
                    : state.ProjectId != request.ProjectId || state.Path != path || state.Guid != AssetDatabase.AssetPathToGUID(path))
                    throw new InvalidOperationException($"'{path}' has no matching SmartUI Sprite ownership record.");
                if (state != null)
                {
                    var existingImporter = AssetImporter.GetAtPath(path) as TextureImporter;
                    if (existingImporter != null && existingImporter.spritePixelsPerUnit != DesignPixelsPerUnit)
                        throw new InvalidOperationException($"'{path}' TextureImporter.spritePixelsPerUnit expected {DesignPixelsPerUnit} from the SmartUI design pixel scale for '{asset.Path}', actual {existingImporter.spritePixelsPerUnit}.");
                    // Report which import setting drifted before reporting the .meta digest conflict.
                    if (files.MetaSha256 != state.Files.MetaSha256)
                        LoadSprite(asset);
                    BuildFiles.RequireSnapshot(path, files, state.Files);
                    PrefabAssets.RequireClean(path, AssetImporter.GetAtPath(path));
                }
                if (!verifying && (files.Sha256 != asset.Sha256 ||
                    SpriteSettingsDiffer(asset, AssetImporter.GetAtPath(path) as TextureImporter)))
                    BuildFiles.RequireWritable(path);
                if (verifying)
                {
                    if (files.Sha256 != asset.Sha256)
                        throw new InvalidOperationException($"Generated Sprite '{path}' does not match its PNG source.");
                    LoadSprite(asset);
                }
            }
        }

        internal void ImportImages()
        {
            foreach (var item in request.Assets.Where(pair => pair.Value.OutputPath != null))
            {
                var asset = item.Value;
                var path = asset.OutputPath;
                var output = BuildFiles.RelativePath(path);
                var statePath = BuildFiles.StatePath(request.DataRoot, "sprites", item.Key);
                var state = BuildFiles.Read<SpriteState>(statePath);
                var importer = AssetImporter.GetAtPath(path) as TextureImporter;
                bool needsSettings = SpriteSettingsDiffer(asset, importer);
                bool needsCopy = PrefabAssets.FileHash(output) != asset.Sha256;
                if (!needsSettings && !needsCopy)
                {
                    LoadSprite(asset);
                    continue;
                }

                BuildFiles.RequireWritable(path);
                BuildFiles.MarkPending(statePath, path);
                PrefabAssets.EnsureOutputDirectory(path);
                if (needsCopy)
                {
                    File.Copy(BuildFiles.RelativePath(asset.Path), output, state != null);
                    AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                }
                if (needsSettings)
                {
                    importer = AssetImporter.GetAtPath(path) as TextureImporter
                        ?? throw new InvalidOperationException($"Unity did not create a texture importer for '{path}'.");
                    importer.textureType = TextureImporterType.Sprite;
                    importer.spriteImportMode = SpriteImportMode.Single;
                    importer.spriteBorder = Borders(asset);
                    importer.spritePixelsPerUnit = DesignPixelsPerUnit;
                    var settings = new TextureImporterSettings();
                    importer.ReadTextureSettings(settings);
                    settings.spriteMeshType = SpriteMeshType.FullRect;
                    importer.SetTextureSettings(settings);
                    importer.alphaIsTransparency = true;
                    importer.mipmapEnabled = false;
                    importer.npotScale = TextureImporterNPOTScale.None;
                    importer.textureCompression = TextureImporterCompression.Uncompressed;
                    importer.maxTextureSize = MaxTextureSize(asset);
                    importer.SaveAndReimport();
                }
                LoadSprite(asset);
                var guid = AssetDatabase.AssetPathToGUID(path);
                if (state != null && state.Guid != guid)
                    throw new InvalidOperationException($"Generated Sprite '{path}' lost its GUID '{state.Guid}'.");
                BuildFiles.Complete(statePath, new SpriteState
                {
                    ProjectId = request.ProjectId, Path = path, Guid = guid, Files = BuildFiles.Snapshot(path)
                });
            }
        }

        static int MaxTextureSize(ImageAsset asset) => Mathf.NextPowerOfTwo(Mathf.Max(asset.Width, asset.Height, 32));

        static bool SpriteSettingsDiffer(ImageAsset asset, TextureImporter importer)
        {
            if (importer == null || importer.textureType != TextureImporterType.Sprite ||
                importer.spriteImportMode != SpriteImportMode.Single || importer.spriteBorder != Borders(asset) ||
                importer.spritePixelsPerUnit != DesignPixelsPerUnit || importer.maxTextureSize != MaxTextureSize(asset))
                return true;
            var settings = new TextureImporterSettings();
            importer.ReadTextureSettings(settings);
            return settings.spriteMeshType != SpriteMeshType.FullRect;
        }

        internal (TextStyle style, TMP_FontAsset font, Material material) Font(string id)
        {
            if (fonts.TryGetValue(id, out var cached))
                return cached;
            if (!request.Fonts.TryGetValue(id, out var style))
                throw new InvalidOperationException($"No TMP font mapping for text style '{id}'.");
            var font = LoadResource<TMP_FontAsset>(style.FontAssetPath, style.FontAssetGuid, style.FontAssetSha256, style.FontAssetMetaSha256);
            var material = LoadResource<Material>(style.MaterialPath, style.MaterialGuid, style.MaterialSha256, style.MaterialMetaSha256);
            if (font.faceInfo.pointSize <= 0 || font.atlasTexture == null || material.mainTexture != font.atlasTexture ||
                !(style.FontSize > 0) || !(style.LineHeight > 0) || float.IsInfinity(style.FontSize) ||
                float.IsInfinity(style.LineHeight) || float.IsInfinity(style.LetterSpacing) || float.IsNaN(style.LetterSpacing) ||
                style.Fill == null)
                throw new InvalidOperationException($"TMP font and material for '{id}' must share a valid font atlas.");
            var mapping = (style, font, material);
            fonts.Add(id, mapping);
            return mapping;
        }

        static T LoadResource<T>(string path, string guid, string sha, string metaSha) where T : Object
        {
            PrefabAssets.RequireAssetPath(path);
            if (sha == null || metaSha == null)
                throw new InvalidOperationException($"'{path}' requires recorded asset and .meta hashes.");
            BuildFiles.RequireSnapshot(path, BuildFiles.Snapshot(path), new FileSnapshot { Sha256 = sha, MetaSha256 = metaSha });
            var assets = AssetDatabase.LoadAllAssetsAtPath(path).OfType<T>().ToArray();
            if (assets.Length != 1 || AssetDatabase.AssetPathToGUID(path) != guid)
                throw new InvalidOperationException($"'{path}' must resolve to one {typeof(T).Name} under GUID '{guid}'.");
            PrefabAssets.RequireClean(path, assets[0]);
            return assets[0];
        }
    }
}
