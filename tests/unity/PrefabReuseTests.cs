using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
using Object = UnityEngine.Object;

namespace SmartUI.UGUI.Editor.Tests
{
    public sealed class PrefabReuseTests
    {
        string id, dataRoot, output, fixtureDirectory, fixtureAssets;
        byte[] projectBefore;
        readonly List<string> operationDirectories = new();

        public static object RunAll()
        {
            var tests = new PrefabReuseTests();
            var cases = new Action[]
            {
                tests.OwnedNodesAndNestedInstanceRootsUseDifferentNames,
                tests.RejectsInvalidProjectFileOrIdentity,
                tests.RejectsReceiptWithoutSchemaVersion,
                tests.ImportsExistingOutputFoldersWithoutRenaming,
                tests.ReusesSavedPrefabObjectsAndRestoresOnlyTheLocalOverride,
                tests.ReportsManagedPrefabChangesBeforeApplyingAnyTarget,
                tests.RejectsReadOnlySpriteMetaBeforeAnImporterSettingsUpdate,
            };
            foreach (var test in cases)
            {
                try
                {
                    tests.SetUp();
                    test();
                }
                finally
                {
                    tests.TearDown();
                }
            }
            return new { passed = cases.Select(test => test.Method.Name).ToArray() };
        }

        void SetUp()
        {
            id = "fixture." + Guid.NewGuid().ToString("N");
            Assert.That(EditorSettings.serializationMode, Is.EqualTo(SerializationMode.ForceText));
            dataRoot = "smart-ui";
            output = "Assets/SmartUI";
            fixtureAssets = output + "/TestFixtures/" + id;
            string projectFile = BuildFiles.RelativePath(dataRoot + "/project.json");
            projectBefore = File.Exists(projectFile) ? File.ReadAllBytes(projectFile) : null;
            BuildFiles.Write(projectFile, new { project_id = id }, replace: true);
            fixtureDirectory = dataRoot + "/unity/tests/" + id;
            Directory.CreateDirectory(BuildFiles.RelativePath(fixtureDirectory));
            WritePng("normal", Color.white);
            WritePng("alternate", Color.blue);
            WritePng("changed", Color.green);
        }

        void TearDown()
        {
            foreach (var directory in new[] { fixtureAssets, output + "/Controls/" + id,
                output + "/Pages/" + id, output + "/Sprites/" + id })
                if (AssetDatabase.IsValidFolder(directory))
                    Assert.That(AssetDatabase.DeleteAsset(directory), Is.True);
            foreach (var directory in operationDirectories.Append(fixtureDirectory))
                if (Directory.Exists(BuildFiles.RelativePath(directory)))
                    Directory.Delete(BuildFiles.RelativePath(directory), true);
            operationDirectories.Clear();
            foreach (var kind in new[] { "base", "variant", "page" })
                File.Delete(BuildFiles.StatePath(dataRoot, kind, id + "." + kind));
            foreach (var key in new[] { "normal", "alternate", "changed" })
                File.Delete(BuildFiles.StatePath(dataRoot, "sprites", id + "." + key));
            string projectFile = BuildFiles.RelativePath(dataRoot + "/project.json");
            if (projectBefore == null)
                File.Delete(projectFile);
            else
                File.WriteAllBytes(projectFile, projectBefore);
            dataRoot = null;
        }

        void OwnedNodesAndNestedInstanceRootsUseDifferentNames()
        {
            Assert.That(PrefabGraph.NodeName("Instance_first"), Is.Not.EqualTo(PrefabGraph.InstanceName("first")));
            Assert.Throws<InvalidOperationException>(() => PrefabAssets.RequireAssetPath(
                output + "/" + PrefabGraph.NodeName("first") + ".prefab", true));
        }

        void RejectsInvalidProjectFileOrIdentity()
        {
            string requestId = Guid.NewGuid().ToString("N");
            string directory = OperationDirectory(requestId);
            string requestPath = BuildFiles.RelativePath(directory + "/request.json");
            string resultPath = directory + "/result.json";
            var request = new BuildRequest
            {
                SchemaVersion = PrefabBuild.SchemaVersion, RequestId = requestId, ProjectId = id,
                DataRoot = dataRoot, ProjectFile = "Assets/other/project.json", ResultPath = resultPath
            };
            BuildFiles.Write(requestPath, request);
            Assert.Throws<InvalidOperationException>(() => PrefabBuild.Execute(requestPath));
            request.ProjectFile = dataRoot + "/project.json";
            request.ProjectId = null;
            BuildFiles.Write(requestPath, request, replace: true);
            Assert.Throws<InvalidOperationException>(() => PrefabBuild.Execute(requestPath));
            Assert.That(File.Exists(BuildFiles.RelativePath(resultPath)), Is.False);
            Assert.Throws<InvalidOperationException>(() => BuildFiles.RequireOutputPath("Sprites", dataRoot + "/image.png"));
            Assert.Throws<InvalidOperationException>(() => BuildFiles.RequireOutputPath("Sprites", output + "/Sprites-other/image.png"));
        }

        void RejectsReceiptWithoutSchemaVersion()
        {
            string requestId = Guid.NewGuid().ToString("N");
            string directory = OperationDirectory(requestId);
            string requestPath = BuildFiles.RelativePath(directory + "/request.json");
            string resultPath = directory + "/result.json";
            BuildFiles.Write(requestPath, new BuildRequest
            {
                SchemaVersion = PrefabBuild.SchemaVersion, RequestId = requestId, ProjectId = id,
                DataRoot = dataRoot, ProjectFile = dataRoot + "/project.json", ResultPath = resultPath
            });
            BuildFiles.Write(BuildFiles.RelativePath(resultPath), new
            {
                request_id = requestId, request_sha256 = PrefabAssets.FileHash(requestPath), status = "success"
            });
            Assert.Throws<InvalidOperationException>(() => PrefabBuild.Execute(requestPath));
        }

        void ImportsExistingOutputFoldersWithoutRenaming()
        {
            string nested = fixtureAssets + "/Nested";
            Directory.CreateDirectory(BuildFiles.RelativePath(nested));
            string physicalRoot = BuildFiles.RelativePath(fixtureAssets);
            var existing = Directory.GetDirectories(physicalRoot, "*", SearchOption.AllDirectories);
            Assert.That(AssetDatabase.IsValidFolder(fixtureAssets), Is.False);
            Assert.That(AssetDatabase.IsValidFolder(nested), Is.False);

            PrefabAssets.EnsureOutputDirectory(nested + "/control.prefab");

            Assert.That(AssetDatabase.IsValidFolder(fixtureAssets), Is.True);
            Assert.That(AssetDatabase.IsValidFolder(nested), Is.True);
            Assert.That(Directory.GetDirectories(physicalRoot, "*", SearchOption.AllDirectories), Is.EquivalentTo(existing));
        }

        void ReusesSavedPrefabObjectsAndRestoresOnlyTheLocalOverride()
        {
            var plan = Run("plan", false, true);
            Assert.That(plan.Targets.Select(item => item.Status), Is.EqualTo(new[] { "create", "create", "create" }));
            Assert.That(File.Exists(BuildFiles.RelativePath(TargetPath("base"))), Is.False);

            var initial = Run("apply", false, true);
            Assert.That(initial.Targets.Select(item => item.Status), Is.EqualTo(new[] { "created", "created", "created" }));
            var variant = AssetDatabase.LoadAssetAtPath<GameObject>(TargetPath("variant"));
            Assert.That(PrefabUtility.GetPrefabAssetType(variant), Is.EqualTo(PrefabAssetType.Variant));
            Assert.That(initial.Targets[1].SlotBindings, Is.Empty);
            var initialPage = initial.Targets[2];
            Assert.That(initialPage.Instances["first"]["background"].ScopeFileId,
                Is.Not.EqualTo(initialPage.Instances["second"]["background"].ScopeFileId));
            Assert.That(initialPage.Instances["first"]["background"].FileId,
                Is.EqualTo(initialPage.Instances["second"]["background"].FileId));
            AssertPage("normal", "normal", "normal");
            string scenePath = SaveReferenceScene();

            var updated = Run("apply", true, true);
            Assert.That(updated.Targets.Select(item => item.Status), Is.EqualTo(new[] { "updated", "verified", "verified" }));
            Assert.That(updated.Targets[1].Sha256, Is.EqualTo(initial.Targets[1].Sha256));
            Assert.That(updated.Targets[1].MetaSha256, Is.EqualTo(initial.Targets[1].MetaSha256));
            Assert.That(updated.Targets[2].Sha256, Is.EqualTo(initialPage.Sha256));
            Assert.That(updated.Targets[2].MetaSha256, Is.EqualTo(initialPage.MetaSha256));
            AssertPage("normal", "changed", "normal");

            var reverted = Run("apply", true, false);
            Assert.That(reverted.Targets.Select(item => item.Status), Is.EqualTo(new[] { "unchanged", "unchanged", "updated" }));
            Assert.That(reverted.Targets[2].Instances["first"]["background"].ScopeFileId,
                Is.EqualTo(initialPage.Instances["first"]["background"].ScopeFileId));
            Assert.That(reverted.Targets[2].Instances["second"]["background"].ScopeFileId,
                Is.EqualTo(initialPage.Instances["second"]["background"].ScopeFileId));
            AssertPage("normal", "changed", "alternate");
            AssertSceneReferences(scenePath);

            string baseline = PrefabAssets.FileHash(BuildFiles.StatePath(dataRoot, "page", id + ".page"));
            var repeated = Run("apply", true, false);
            Assert.That(repeated.Targets.Select(item => item.Status), Is.All.EqualTo("unchanged"));
            Assert.That(repeated.Targets[2].Sha256, Is.EqualTo(reverted.Targets[2].Sha256));
            Assert.That(Run("verify", true, false).Targets.Select(item => item.Status), Is.All.EqualTo("verified"));
            Assert.That(PrefabAssets.FileHash(BuildFiles.StatePath(dataRoot, "page", id + ".page")), Is.EqualTo(baseline));

            var spritePath = SpritePath("normal");
            string spriteGuid = AssetDatabase.AssetPathToGUID(spritePath);
            WritePng("normal", Color.red);
            var replaced = Run("apply", true, false, changedSource: true);
            Assert.That(replaced.Targets.Select(item => item.Status), Is.All.EqualTo("verified"));
            Assert.That(AssetDatabase.AssetPathToGUID(spritePath), Is.EqualTo(spriteGuid));
            Assert.That(replaced.Targets.Select(item => item.Sha256), Is.EqualTo(repeated.Targets.Select(item => item.Sha256)));
        }

        void ReportsManagedPrefabChangesBeforeApplyingAnyTarget()
        {
            var initial = Run("apply", false, true);
            var root = PrefabUtility.LoadPrefabContents(TargetPath("page"));
            try
            {
                var rect = (RectTransform)root.transform.Find(PrefabGraph.NodeName("first"));
                rect.anchoredPosition += Vector2.right;
                PrefabUtility.SaveAsPrefabAsset(root, TargetPath("page"), out bool saved);
                Assert.That(saved, Is.True);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(root);
            }
            var result = Run("apply", true, true, false);
            Assert.That(result.Targets[2].Error, Does.Contain("first/rect"));
            Assert.That(BuildFiles.Snapshot(TargetPath("base")).Sha256, Is.EqualTo(initial.Targets[0].Sha256));
        }

        void RejectsReadOnlySpriteMetaBeforeAnImporterSettingsUpdate()
        {
            Run("apply", false, true);
            string normalPath = SpritePath("normal");
            string metaPath = BuildFiles.RelativePath(SpritePath("changed") + ".meta");
            string originalNormal = BuildFiles.Snapshot(normalPath).Sha256;
            var attributes = File.GetAttributes(metaPath);
            File.SetAttributes(metaPath, attributes | FileAttributes.ReadOnly);
            try
            {
                WritePng("normal", Color.red);
                foreach (string operation in new[] { "plan", "apply" })
                {
                    var failed = Run(operation, false, true, success: false, changedSource: true, changedBorder: true);
                    Assert.That(failed.Error, Does.Contain("changed.png.meta"));
                    Assert.That(BuildFiles.Snapshot(normalPath).Sha256, Is.EqualTo(originalNormal));
                }
            }
            finally
            {
                File.SetAttributes(metaPath, attributes);
            }
        }

        BuildResult Run(string operation, bool updateBase, bool pageOverride, bool success = true, bool changedSource = false,
            bool changedBorder = false)
        {
            var requestId = Guid.NewGuid().ToString("N");
            string directory = OperationDirectory(requestId);
            string requestPath = BuildFiles.RelativePath(directory + "/request.json");
            string resultPath = directory + "/result.json";
            var slots = new Dictionary<string, Slot>
            {
                ["background"] = new Slot { NodeId = "art", Component = "image" },
                ["click"] = new Slot { NodeId = "root", Component = "button" }
            };
            var parameters = new Dictionary<string, Parameter>
            {
                ["art"] = new Parameter { SlotId = "background", Property = "asset_id", ValueType = "asset_id" }
            };
            Control ControlMapping(string kind) => new()
            {
                Path = TargetPath(kind), Slots = slots, Width = 24, Height = 24, Parameters = parameters,
                SlotSources = new Dictionary<string, SlotSource>()
            };
            var normal = id + (updateBase ? ".changed" : ".normal");
            Node Instance(string nodeId, string control, float x, bool explicitArt) => new()
            {
                Id = nodeId, Kind = "control", ControlId = id + "." + control, X = x, Width = 24, Height = 24,
                Parameters = explicitArt ? new Dictionary<string, JToken> { ["art"] = id + ".normal" } : new()
            };
            Target MakeTarget(string kind, char direct, char dependency) => new()
            {
                Id = id + "." + kind, Kind = kind, Path = TargetPath(kind), Width = kind == "page" ? 84 : 24,
                Height = 24, Slots = kind == "page" ? new() : slots, Nodes = Array.Empty<Node>(), Overrides = Array.Empty<NodeOverride>(),
                DirectSha256 = new string(direct, 64), DependencySha256 = new string(changedSource ? 'c' : dependency, 64),
                Expected = TargetExpectation(kind)
            };
            var main = MakeTarget("base", updateBase ? 'b' : 'a', 'a');
            main.Nodes = new[] { new Node { Id = "art", Kind = "image", AssetId = normal, Width = 24, Height = 24 } };
            main.Button = new ButtonSpec { TargetGraphic = "art", States = new Dictionary<string, string> { ["normal"] = normal, ["pressed"] = id + ".alternate" } };
            var variant = MakeTarget("variant", 'a', updateBase ? 'b' : 'a');
            variant.BaseControlId = id + ".base";
            variant.Overrides = new[] { new NodeOverride { NodeId = "art", Property = "asset_id", Value = id + ".alternate" } };
            var page = MakeTarget("page", pageOverride ? 'a' : 'b', updateBase ? 'b' : 'a');
            page.Background = "#112233";
            page.Nodes = new[] { Instance("first", "base", 0, true), Instance("second", "base", 30.1f, false), Instance("third", "variant", 60, pageOverride) };
            var request = new BuildRequest
            {
                SchemaVersion = PrefabBuild.SchemaVersion, ProjectId = id, RequestId = requestId, Operation = operation,
                DataRoot = dataRoot, ProjectFile = dataRoot + "/project.json", ResultPath = resultPath,
                Targets = new[] { main, variant, page }, Fonts = new(),
                Controls = new Dictionary<string, Control> { [id + ".base"] = ControlMapping("base"), [id + ".variant"] = ControlMapping("variant") },
                Assets = new[] { "normal", "alternate", "changed" }.ToDictionary(key => id + "." + key, key => ImageMapping(key))
            };
            if (changedBorder)
            {
                request.Assets[id + ".changed"].Geometry = new JObject
                {
                    ["mode"] = "nine_slice",
                    ["borders"] = new JObject { ["left"] = 2, ["top"] = 2, ["right"] = 2, ["bottom"] = 2 }
                };
            }
            BuildFiles.Write(requestPath, request);
            var receipt = JObject.FromObject(PrefabBuild.Execute(requestPath));
            Assert.That((string)receipt["request_id"], Is.EqualTo(requestId));
            Assert.That((string)receipt["result_path"], Is.EqualTo(resultPath));
            Assert.That((string)receipt["status"], Is.EqualTo(success ? "success" : "failed"), File.ReadAllText(BuildFiles.RelativePath(resultPath)));
            var result = BuildFiles.Read<BuildResult>(BuildFiles.RelativePath(resultPath));
            Assert.That(result.SchemaVersion, Is.EqualTo(PrefabBuild.SchemaVersion));
            Assert.That(result.RequestSha256, Is.EqualTo(PrefabAssets.FileHash(requestPath)));
            return result;
        }

        string OperationDirectory(string requestId)
        {
            string path = dataRoot + "/unity/operations/" + requestId;
            operationDirectories.Add(path);
            return path;
        }

        string TargetPath(string kind) => output + (kind == "page" ? "/Pages/" : "/Controls/") + id + "/" + kind + ".prefab";

        string SpritePath(string key) => output + "/Sprites/" + id + "/" + key + ".png";

        ExpectedTarget TargetExpectation(string kind)
        {
            var actual = BuildFiles.Snapshot(TargetPath(kind));
            return new ExpectedTarget
            {
                PrefabSha256 = actual.Sha256, MetaSha256 = actual.MetaSha256,
                BaselineSha256 = PrefabAssets.FileHash(BuildFiles.StatePath(dataRoot, kind, id + "." + kind))
            };
        }

        void WritePng(string name, Color color)
        {
            var texture = new Texture2D(16, 16, TextureFormat.RGBA32, false);
            try
            {
                texture.SetPixels(Enumerable.Repeat(color, 256).ToArray());
                texture.Apply();
                File.WriteAllBytes(BuildFiles.RelativePath(fixtureDirectory + "/" + name + ".png"), texture.EncodeToPNG());
            }
            finally
            {
                Object.DestroyImmediate(texture);
            }
        }

        ImageAsset ImageMapping(string key)
        {
            var source = fixtureDirectory + "/" + key + ".png";
            var target = SpritePath(key);
            return new ImageAsset
            {
                Path = source, OutputPath = target, Width = 16, Height = 16,
                Sha256 = PrefabAssets.FileHash(BuildFiles.RelativePath(source)), Expected = BuildFiles.Snapshot(target)
            };
        }

        static Image Art(GameObject page, string node) => page.transform.Find(PrefabGraph.NodeName(node) + "/" +
            PrefabGraph.InstanceName(node) + "/" + PrefabGraph.NodeName("art")).GetComponent<Image>();

        void AssertPage(string first, string second, string third)
        {
            var page = AssetDatabase.LoadAssetAtPath<GameObject>(TargetPath("page"));
            Assert.That((Color32)page.GetComponent<Image>().color, Is.EqualTo(new Color32(17, 34, 51, 255)));
            Assert.That(Art(page, "first").sprite.name, Is.EqualTo(first));
            Assert.That(Art(page, "second").sprite.name, Is.EqualTo(second));
            Assert.That(Art(page, "third").sprite.name, Is.EqualTo(third));
        }

        string SaveReferenceScene()
        {
            var active = SceneManager.GetActiveScene();
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            string path = fixtureAssets + "/references.unity";
            PrefabAssets.EnsureOutputDirectory(path);
            try
            {
                var page = PrefabAssets.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(TargetPath("page")), scene);
                foreach (var node in new[] { "first", "second" })
                {
                    var holder = PrefabAssets.NewRect("reference_" + node, scene);
                    holder.AddComponent<Button>().targetGraphic = Art(page, node);
                }
                Assert.That(EditorSceneManager.SaveScene(scene, path), Is.True);
            }
            finally
            {
                EditorSceneManager.CloseScene(scene, true);
                if (active.IsValid())
                    SceneManager.SetActiveScene(active);
            }
            return path;
        }

        static void AssertSceneReferences(string path)
        {
            var active = SceneManager.GetActiveScene();
            var scene = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
            try
            {
                var roots = scene.GetRootGameObjects().ToDictionary(item => item.name);
                foreach (var node in new[] { "first", "second" })
                    Assert.That(roots["reference_" + node].GetComponent<Button>().targetGraphic, Is.SameAs(Art(roots["page"], node)));
            }
            finally
            {
                EditorSceneManager.CloseScene(scene, true);
                if (active.IsValid())
                    SceneManager.SetActiveScene(active);
            }
        }
    }
}
