using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using TMPro;
using UnityEditor;
using UnityEngine;
using ImageComponent = UnityEngine.UI.Image;
using Object = UnityEngine.Object;

namespace SmartUI.UGUI.Editor
{
    internal sealed class BuildResources
    {
        readonly BuildRequest request;
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
            if (sprites.Length != 1 || sprites[0].rect != new Rect(0, 0, asset.Width, asset.Height) ||
                sprites[0].border != Borders(asset) || (Sliced(asset) && sprites[0].pixelsPerUnit != 100))
                throw new InvalidOperationException($"'{path}' must import as a single full-size Sprite with the declared borders and 100 pixels per unit when sliced.");
            PrefabAssets.RequireClean(path, sprites[0]);
            return sprites[0];
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
                BuildFiles.RequireOutputPath(request.DataRoot, path);

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
                    importer.spritePixelsPerUnit = 100;
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
                importer.spritePixelsPerUnit != 100 || importer.maxTextureSize != MaxTextureSize(asset))
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
