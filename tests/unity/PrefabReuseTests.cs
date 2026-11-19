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
                tests.PreflightsVariantResizeAgainstItsCurrentBase,
                tests.ReportsManagedPrefabChangesBeforeApplyingAnyTarget,
                tests.RejectsReadOnlySpriteMetaBeforeAnImporterSettingsUpdate,
                tests.ImportsReadOnlyExportAndUpdatesItsManagedSprite,
                tests.ReusesRegisteredExportAndPreservesItsSpriteIdentity,
                tests.ChecksSlicedScaleAndAdoptsOnlyMatchingLegacyValues,
                tests.TracksMultiplierOnlyWhileDesignUsesSlicing,
                tests.ReportsEditedSpriteSettingsWithTheirExportSource,
                tests.ReadsHostCanvasWithoutChangingResolutionAdaptation,
                tests.ChecksLoadedSlicedImagesAgainstTheirEffectivePrefab,
                tests.ChecksSavedNestedArtworkAgainstItsDeclaration,
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
                DeleteState(kind, id + "." + kind);
            foreach (var key in new[] { "normal", "alternate", "changed" })
                DeleteState("sprites", id + "." + key);
            string directSprite = output + "/Sprites/" + id + ".normal.png";
            if (File.Exists(BuildFiles.RelativePath(directSprite)))
                Assert.That(AssetDatabase.DeleteAsset(directSprite), Is.True);
            string directRecord = BuildFiles.RelativePath(dataRoot + "/sources/images/" + id + ".normal.json");
            if (File.Exists(directRecord))
                File.Delete(directRecord);
            string projectFile = BuildFiles.RelativePath(dataRoot + "/project.json");
            if (projectBefore == null)
                File.Delete(projectFile);
            else
                File.WriteAllBytes(projectFile, projectBefore);
            dataRoot = null;

            void DeleteState(string kind, string key)
            {
                var path = BuildFiles.StatePath(dataRoot, kind, key);
                if (File.Exists(path))
                    File.Delete(path);
            }
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

        void PreflightsVariantResizeAgainstItsCurrentBase()
        {
            var original = Run("apply", false, true);
            var planned = Run("plan", false, true, resized: true);
            Assert.That(planned.Targets[1].Status, Is.EqualTo("verify"));
            var updated = Run("apply", false, true, resized: true);
            Assert.That(updated.Targets.Select(item => item.Status), Is.EqualTo(new[] { "updated", "verified" }));
            Assert.That(updated.Targets[1].Sha256, Is.EqualTo(original.Targets[1].Sha256));
            var variant = AssetDatabase.LoadAssetAtPath<GameObject>(TargetPath("variant"));
            Assert.That(((RectTransform)variant.transform).rect.size, Is.EqualTo(new Vector2(32, 28)));
            Run("verify", false, true, resized: true);

            // A prior tool's default root override and an explicit equal-value edit serialize identically.
            // Both remain valid at the original size and must survive a proposed resize unchanged.
            var edited = PrefabUtility.LoadPrefabContents(TargetPath("variant"));
            try
            {
                var existing = PrefabUtility.GetPropertyModifications(edited);
                var equalWidth = new PropertyModification
                {
                    target = PrefabUtility.GetCorrespondingObjectFromSource(edited.transform),
                    propertyPath = "m_SizeDelta.x", value = "32"
                };
                PrefabUtility.SetPropertyModifications(edited, existing.Append(equalWidth).ToArray());
                PrefabUtility.SaveAsPrefabAsset(edited, TargetPath("variant"), out bool saved);
                Assert.That(saved, Is.True);
            }
            finally { PrefabUtility.UnloadPrefabContents(edited); }
            Run("verify", false, true, resized: true);
            var equalOverride = BuildFiles.Snapshot(TargetPath("variant"));
            var held = Run("apply", false, true, success: false);
            Assert.That(held.Targets[1].Error, Does.Contain("m_SizeDelta.x").And.Contain(TargetPath("base")));
            Assert.That(BuildFiles.Snapshot(TargetPath("variant")).Sha256, Is.EqualTo(equalOverride.Sha256));
            Assert.That(BuildFiles.Snapshot(TargetPath("base")).Sha256, Is.EqualTo(updated.Targets[0].Sha256));

            edited = PrefabUtility.LoadPrefabContents(TargetPath("variant"));
            try
            {
                ((RectTransform)edited.transform).sizeDelta = new Vector2(31, 27);
                PrefabUtility.RecordPrefabInstancePropertyModifications(edited.transform);
                PrefabUtility.SaveAsPrefabAsset(edited, TargetPath("variant"), out bool saved);
                Assert.That(saved, Is.True);
            }
            finally { PrefabUtility.UnloadPrefabContents(edited); }
            var manual = BuildFiles.Snapshot(TargetPath("variant"));
            var failed = Run("apply", false, true, success: false);
            Assert.That(failed.Targets[1].Error, Does.Contain(TargetPath("base")).And.Contain("actual"));
            Assert.That(BuildFiles.Snapshot(TargetPath("base")).Sha256, Is.EqualTo(updated.Targets[0].Sha256));
            Assert.That(BuildFiles.Snapshot(TargetPath("variant")).Sha256, Is.EqualTo(manual.Sha256));
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

        void ImportsReadOnlyExportAndUpdatesItsManagedSprite()
        {
            string source = BuildFiles.RelativePath(fixtureDirectory + "/normal.png");
            string path = SpritePath("normal");
            string copy = BuildFiles.RelativePath(path);
            string statePath = BuildFiles.StatePath(dataRoot, "sprites", id + ".normal");
            string pendingPath = statePath + ".pending";
            var sourceAttributes = File.GetAttributes(source);
            File.SetAttributes(source, sourceAttributes | FileAttributes.ReadOnly);
            try
            {
                Run("apply", false, false);
                Assert.That(AssetDatabase.LoadAssetAtPath<Sprite>(path), Is.Not.Null);
                Assert.That(File.GetAttributes(copy).HasFlag(FileAttributes.ReadOnly), Is.False);
                Assert.That(File.GetAttributes(source).HasFlag(FileAttributes.ReadOnly), Is.True);
                Assert.That(File.Exists(pendingPath), Is.False);
                var initial = BuildFiles.Read<SpriteState>(statePath);
                Assert.That(initial.Files.Sha256, Is.EqualTo(PrefabAssets.FileHash(source)));

                File.SetAttributes(source, sourceAttributes);
                WritePng("normal", Color.red);
                File.SetAttributes(source, sourceAttributes | FileAttributes.ReadOnly);
                Run("apply", false, false, changedSource: true);
                string updatedHash = PrefabAssets.FileHash(source);
                Assert.That(updatedHash, Is.Not.EqualTo(initial.Files.Sha256));
                Assert.That(PrefabAssets.FileHash(copy), Is.EqualTo(updatedHash));
                Assert.That(File.GetAttributes(copy).HasFlag(FileAttributes.ReadOnly), Is.False);
                Assert.That(File.GetAttributes(source).HasFlag(FileAttributes.ReadOnly), Is.True);
                Assert.That(File.Exists(pendingPath), Is.False);
                var updated = BuildFiles.Read<SpriteState>(statePath);
                Assert.That(updated.Files.Sha256, Is.EqualTo(updatedHash));
                Assert.That(updated.Guid, Is.EqualTo(initial.Guid));
                Assert.That(AssetDatabase.LoadAssetAtPath<Sprite>(path), Is.Not.Null);
            }
            finally
            {
                File.SetAttributes(source, sourceAttributes);
                if (File.Exists(copy))
                    File.SetAttributes(copy, File.GetAttributes(copy) & ~FileAttributes.ReadOnly);
                File.Delete(pendingPath);
            }
        }

        void ChecksSlicedScaleAndAdoptsOnlyMatchingLegacyValues()
        {
            BuildResult Check(string operation, bool success = true, bool adopt = false) =>
                Run(operation, false, true, success, sliced: true, adoptMatching: adopt);
            Check("apply");
            const string field = "art/m_PixelsPerUnitMultiplier";
            var baselinePath = BuildFiles.StatePath(dataRoot, "base", id + ".base");
            var legacy = BuildFiles.Read<JObject>(baselinePath);
            Assert.That(((JObject)legacy["managed"]).Remove(field), Is.True);
            BuildFiles.Write(baselinePath, legacy, replace: true);
            var legacyHash = PrefabAssets.FileHash(baselinePath);
            var prefabPath = TargetPath("base");
            var original = BuildFiles.Snapshot(prefabPath);
            foreach (string operation in new[] { "plan", "verify", "apply" })
            {
                Assert.That(Check(operation).Targets[0].ManagedDifferences, Is.Null);
                Assert.That(PrefabAssets.FileHash(baselinePath), Is.EqualTo(legacyHash));
            }
            void SetMultiplier(float value)
            {
                var prefab = PrefabUtility.LoadPrefabContents(prefabPath);
                try
                {
                    var image = prefab.GetComponentInChildren<Image>();
                    image.pixelsPerUnitMultiplier = value;
                    image.color = Color.yellow;
                    PrefabUtility.SaveAsPrefabAsset(prefab, prefabPath, out bool saved);
                    Assert.That(saved, Is.True);
                }
                finally { PrefabUtility.UnloadPrefabContents(prefab); }
            }
            SetMultiplier(2);
            var edited = BuildFiles.Snapshot(prefabPath);
            foreach (var (operation, adopt) in new[] { ("plan", false), ("verify", false), ("apply", false), ("apply", true) })
            {
                var failure = Check(operation, false, adopt).Targets[0];
                Assert.That(failure.Error, Does.Contain("expected 1").And.Contain("actual 2").And.Contain("SmartUI_Node:art"));
                Assert.That((float)failure.ManagedDifferences[field]["unity"], Is.EqualTo(2));
                Assert.That((float)failure.ManagedDifferences[field]["design"], Is.EqualTo(1));
                Assert.That(PrefabAssets.FileHash(baselinePath), Is.EqualTo(legacyHash));
                Assert.That(BuildFiles.Snapshot(prefabPath).Sha256, Is.EqualTo(edited.Sha256));
            }
            SetMultiplier(1);
            var restored = BuildFiles.Snapshot(prefabPath);
            Check("apply", adopt: true);
            Assert.That((float)BuildFiles.Read<JObject>(baselinePath)["managed"][field], Is.EqualTo(1));
            Assert.That(BuildFiles.Snapshot(prefabPath).Sha256, Is.EqualTo(restored.Sha256));
            Assert.That(BuildFiles.Snapshot(prefabPath).MetaSha256, Is.EqualTo(original.MetaSha256));
            Assert.That(AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath).GetComponentInChildren<Image>().color, Is.EqualTo(Color.yellow));
        }

        void ReportsEditedSpriteSettingsWithTheirExportSource()
        {
            Run("apply", false, true, sliced: true);
            string path = SpritePath("normal");
            void Conflict(string property)
            {
                var edited = BuildFiles.Snapshot(path);
                var failed = Run("apply", false, true, false, sliced: true);
                Assert.That(failed.Error, Does.Contain(path).And.Contain(property).And.Contain("expected").And.Contain("actual")
                    .And.Contain(fixtureDirectory + "/normal.png"));
                Assert.That(BuildFiles.Snapshot(path).MetaSha256, Is.EqualTo(edited.MetaSha256));
            }
            var importer = (TextureImporter)AssetImporter.GetAtPath(path);
            importer.spritePixelsPerUnit = 200;
            importer.SaveAndReimport();
            Conflict("spritePixelsPerUnit");
            importer = (TextureImporter)AssetImporter.GetAtPath(path);
            importer.spritePixelsPerUnit = 100;
            importer.spriteBorder = new Vector4(1, 2, 3, 4);
            importer.SaveAndReimport();
            Conflict("Sprite.border");
        }

        void TracksMultiplierOnlyWhileDesignUsesSlicing()
        {
            Run("apply", false, true);
            var baselinePath = BuildFiles.StatePath(dataRoot, "base", id + ".base");
            const string field = "art/m_PixelsPerUnitMultiplier";
            JObject Baseline() => (JObject)BuildFiles.Read<JObject>(baselinePath)["managed"];
            Assert.That(Baseline().Property(field), Is.Null);
            Run("apply", false, true, sliced: true);
            Assert.That((float)Baseline()[field], Is.EqualTo(1));
            Run("apply", false, true);
            Assert.That(Baseline().Property(field), Is.Null);
            var prefabPath = TargetPath("base");
            var root = PrefabUtility.LoadPrefabContents(prefabPath);
            try
            {
                root.GetComponentInChildren<Image>().pixelsPerUnitMultiplier = 2;
                PrefabUtility.SaveAsPrefabAsset(root, prefabPath, out bool saved);
                Assert.That(saved, Is.True);
            }
            finally { PrefabUtility.UnloadPrefabContents(root); }
            Run("verify", false, true);
            var before = BuildFiles.Snapshot(prefabPath);
            var conflict = Run("apply", false, true, false, sliced: true);
            Assert.That((float)conflict.Targets[0].ManagedDifferences[field]["unity"], Is.EqualTo(2));
            Assert.That(BuildFiles.Snapshot(prefabPath).Sha256, Is.EqualTo(before.Sha256));
        }

        void ReadsHostCanvasWithoutChangingResolutionAdaptation()
        {
            var initial = Run("apply", false, true, sliced: true);
            Assert.That(initial.Targets[0].CanvasReferencePixelsPerUnit, Is.Empty);
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                var host = PrefabAssets.NewRect("ScaleTestCanvas", scene);
                var canvas = host.AddComponent<Canvas>();
                var scaler = host.AddComponent<CanvasScaler>();
                scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
                scaler.referenceResolution = new Vector2(800, 600);
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(TargetPath("base"));
                var instance = PrefabAssets.Instantiate(prefab, scene, host.transform);
                instance.transform.localScale = new Vector3(0.8f, 0.8f, 1f);
                var verified = Run("verify", false, true, sliced: true);
                Assert.That(verified.Targets[0].CanvasReferencePixelsPerUnit.Values, Does.Contain(100f));
                scaler.referencePixelsPerUnit = 75;
                var conflict = Run("plan", false, true, false, sliced: true);
                Assert.That(conflict.Error, Does.Contain("ScaleTestCanvas").And.Contain("expected 100").And.Contain("actual 75"));
                Assert.That(scaler.referencePixelsPerUnit, Is.EqualTo(75f));
                Assert.That(scaler.uiScaleMode, Is.EqualTo(CanvasScaler.ScaleMode.ScaleWithScreenSize));
                Assert.That(instance.transform.localScale.x, Is.EqualTo(0.8f));
            }
            finally { EditorSceneManager.CloseScene(scene, true); }
        }

        void ChecksLoadedSlicedImagesAgainstTheirEffectivePrefab()
        {
            BuildResult Verify(bool success = true) => Run("verify", false, false, success, sliced: true);
            Run("apply", false, false, sliced: true);
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            var foreign = Sprite.Create(Texture2D.whiteTexture, new Rect(0, 0, 2, 2), Vector2.zero, 200);
            try
            {
                var outer = PrefabAssets.NewRect("OuterCanvas", scene).AddComponent<Canvas>();
                var disabled = PrefabAssets.NewRect("DisabledCanvas", scene, outer.transform).AddComponent<Canvas>();
                disabled.enabled = false;
                disabled.referencePixelsPerUnit = 75;
                var instance = PrefabAssets.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(TargetPath("page")), scene, disabled.transform);
                var image = Art(instance, "third");
                Assert.That(image.sprite, Is.SameAs(AssetDatabase.LoadAssetAtPath<Sprite>(SpritePath("alternate"))));
                var freeImage = PrefabAssets.NewRect("SmartUI_Node:custom", scene, instance.transform).AddComponent<Image>();
                freeImage.type = Image.Type.Sliced;
                freeImage.pixelsPerUnitMultiplier = 2;
                image.color = Color.gray;
                Verify();
                outer.referencePixelsPerUnit = 75;
                Assert.That(Verify(false).Error, Does.Contain("OuterCanvas").And.Contain("actual 75"));
                outer.referencePixelsPerUnit = 100;
                image.type = Image.Type.Simple;
                Assert.That(Verify(false).Error, Does.Contain("expected Sliced").And.Contain("actual Simple").And.Contain(TargetPath("page")));
                image.type = Image.Type.Sliced;
                image.overrideSprite = foreign;
                Assert.That(Verify(false).Error, Does.Contain("active Sprite").And.Contain("PPU 200").And.Contain("alternate.png"));
                image.overrideSprite = null;
                image.sprite = AssetDatabase.LoadAssetAtPath<Sprite>(SpritePath("changed"));
                Assert.That(Verify(false).Error, Does.Contain("changed.png").And.Contain("alternate.png"));
                image.sprite = null;
                Assert.That(Verify(false).Error, Does.Contain("actual null"));
                image.sprite = AssetDatabase.LoadAssetAtPath<Sprite>(SpritePath("alternate"));
                var first = Art(instance, "first");
                first.overrideSprite = image.sprite; // The declared pressed Sprite is valid while the button is in that state.
                Verify();
                Object.DestroyImmediate(image);
                Assert.That(Verify(false).Error, Does.Contain("expected Sliced Image").And.Contain("Image missing").And.Contain(TargetPath("page")));
            }
            finally
            {
                Object.DestroyImmediate(foreign);
                EditorSceneManager.CloseScene(scene, true);
            }
        }

        void ChecksSavedNestedArtworkAgainstItsDeclaration()
        {
            BuildResult RunSliced(string operation, bool success = true) =>
                Run(operation, false, false, success, sliced: true, privateArtwork: true);
            RunSliced("apply");
            AssertPage("normal", "normal", "alternate");
            string page = TargetPath("page");
            string baseline = BuildFiles.StatePath(dataRoot, "page", id + ".page");
            string baselineHash = PrefabAssets.FileHash(baseline);
            void Edit(Action<Image> change, string imageNode = "art")
            {
                var root = PrefabUtility.LoadPrefabContents(page);
                try
                {
                    var image = Art(root, "third", imageNode);
                    change(image);
                    PrefabUtility.RecordPrefabInstancePropertyModifications(image);
                    PrefabUtility.RecordPrefabInstancePropertyModifications(image.rectTransform);
                    PrefabUtility.SaveAsPrefabAsset(root, page, out bool saved);
                    Assert.That(saved, Is.True);
                }
                finally { PrefabUtility.UnloadPrefabContents(root); }
            }
            void Conflict(string field, string expected, string actual, string imageNode = "art")
            {
                var snapshot = BuildFiles.Snapshot(page);
                var result = RunSliced("verify", false);
                Assert.That(result.Targets[2].Error, Does.Contain(page).And.Contain(PrefabGraph.NodeName(imageNode))
                    .And.Contain(field).And.Contain(expected).And.Contain(actual));
                Assert.That(BuildFiles.Snapshot(page).Sha256, Is.EqualTo(snapshot.Sha256));
                Assert.That(BuildFiles.Snapshot(page).MetaSha256, Is.EqualTo(snapshot.MetaSha256));
                Assert.That(PrefabAssets.FileHash(baseline), Is.EqualTo(baselineHash));
            }
            Edit(image => image.type = Image.Type.Simple);
            Conflict("Image.type", "expected Sliced", "actual Simple");
            Edit(image => { image.type = Image.Type.Sliced; image.sprite = AssetDatabase.LoadAssetAtPath<Sprite>(SpritePath("changed")); });
            Conflict("Image.sprite", "alternate.png", "changed.png");
            Edit(image =>
            {
                PrefabUtility.RevertPropertyOverride(new SerializedObject(image).FindProperty("m_Sprite"), InteractionMode.AutomatedAction);
                image.rectTransform.anchoredPosition += Vector2.right * 3;
            });
            Conflict("RectTransform", "anchoredPosition", "actual");
            Edit(image =>
            {
                image.rectTransform.anchoredPosition -= Vector2.right * 3;
                image.rectTransform.localScale = new Vector3(0.8f, 0.8f, 1f);
            });
            RunSliced("verify");
            // The decoration has neither a public Sprite parameter nor a variant override.
            Edit(image => image.sprite = AssetDatabase.LoadAssetAtPath<Sprite>(SpritePath("changed")), "decoration");
            Conflict("Image.sprite", "normal.png", "changed.png", "decoration");

            string variant = TargetPath("variant");
            var variantRoot = PrefabUtility.LoadPrefabContents(variant);
            try
            {
                var image = variantRoot.transform.Find(PrefabGraph.NodeName("decoration")).GetComponent<Image>();
                image.type = Image.Type.Simple;
                PrefabUtility.RecordPrefabInstancePropertyModifications(image);
                PrefabUtility.SaveAsPrefabAsset(variantRoot, variant, out bool saved);
                Assert.That(saved, Is.True);
            }
            finally { PrefabUtility.UnloadPrefabContents(variantRoot); }
            var variantBefore = BuildFiles.Snapshot(variant);
            var failed = RunSliced("verify", false);
            Assert.That(failed.Targets[1].Error, Does.Contain(variant).And.Contain("expected Sliced").And.Contain("actual Simple"));
            Assert.That(BuildFiles.Snapshot(variant).Sha256, Is.EqualTo(variantBefore.Sha256));
        }

        void ReusesRegisteredExportAndPreservesItsSpriteIdentity()
        {
            string assetId = id + ".normal";
            string path = output + "/Sprites/" + assetId + ".png";
            string recordPath = dataRoot + "/sources/images/" + assetId + ".json";
            string statePath = BuildFiles.StatePath(dataRoot, "sprites", assetId);
            PrefabAssets.EnsureOutputDirectory(path);
            BuildFiles.Write(BuildFiles.RelativePath(dataRoot + "/project.json"), new
            {
                project_id = id, images = new Dictionary<string, string> { [assetId] = recordPath },
            }, replace: true);

            ImageAsset WriteExport(string source, bool register = true)
            {
                File.Copy(BuildFiles.RelativePath(fixtureDirectory + "/" + source + ".png"), BuildFiles.RelativePath(path), true);
                var snapshot = BuildFiles.Snapshot(path);
                if (register)
                    BuildFiles.Write(BuildFiles.RelativePath(recordPath), new
                    {
                        path, output_path = path, sha256 = snapshot.Sha256,
                        provenance = new { kind = "component_export" },
                    }, replace: true);
                return new ImageAsset { Path = path, OutputPath = path, Width = 16, Height = 16,
                    Sha256 = snapshot.Sha256, Expected = snapshot };
            }

            var initial = WriteExport("normal");
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            var importer = (TextureImporter)AssetImporter.GetAtPath(path);
            importer.isReadable = true;
            importer.SaveAndReimport();
            initial.Expected = BuildFiles.Snapshot(path);
            string guid = AssetDatabase.AssetPathToGUID(path);
            Assert.That(guid, Is.Not.Empty);
            EditorUtility.SetDirty(importer);
            Assert.That(EditorUtility.IsDirty(importer), Is.True);
            try
            {
                var dirty = Run("apply", false, false, success: false, directExport: initial);
                Assert.That(dirty.Error, Does.Contain("unsaved changes").And.Contain(path));
                Assert.That(File.Exists(statePath), Is.False);
                Assert.That(File.Exists(statePath + ".pending"), Is.False);
                Assert.That(BuildFiles.Snapshot(TargetPath("base")).Sha256, Is.Null);
                Assert.That(BuildFiles.Snapshot(path).MetaSha256, Is.EqualTo(initial.Expected.MetaSha256));
            }
            finally
            {
                EditorUtility.ClearDirty(importer);
            }
            Run("apply", false, false, directExport: initial);
            Assert.That(AssetDatabase.AssetPathToGUID(path), Is.EqualTo(guid));
            string meta = BuildFiles.Snapshot(path).MetaSha256;
            var before = AssetDatabase.LoadAssetAtPath<Sprite>(path);
            Assert.That(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(before, out _, out long fileId), Is.True);

            Color32 PagePixel() => Art(AssetDatabase.LoadAssetAtPath<GameObject>(TargetPath("page")), "first")
                .sprite.texture.GetPixel(4, 4);
            var updated = WriteExport("changed");
            Assert.That(PagePixel(), Is.EqualTo((Color32)Color.white));
            string previousState = PrefabAssets.FileHash(statePath);
            var stale = Run("verify", false, false, success: false, changedSource: true, directExport: updated);
            Assert.That(stale.Error, Does.Contain(path).And.Contain("apply"));
            var adopt = Run("apply", false, false, success: false, changedSource: true, adoptMatching: true, directExport: updated);
            Assert.That(adopt.Error, Does.Contain(path).And.Contain("apply"));
            Assert.That(PagePixel(), Is.EqualTo((Color32)Color.white));
            Assert.That(PrefabAssets.FileHash(statePath), Is.EqualTo(previousState));
            Run("plan", false, false, changedSource: true, directExport: updated);
            Run("apply", false, false, changedSource: true, directExport: updated);
            Run("verify", false, false, changedSource: true, directExport: updated);
            Assert.That(PagePixel(), Is.EqualTo((Color32)Color.green));
            var state = BuildFiles.Read<SpriteState>(statePath);
            Assert.That(state.Files.Sha256, Is.EqualTo(updated.Sha256));
            Assert.That(state.Files.MetaSha256, Is.EqualTo(meta));
            Assert.That(state.Guid, Is.EqualTo(guid));
            var after = AssetDatabase.LoadAssetAtPath<Sprite>(path);
            Assert.That(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(after, out string stillGuid, out long stillFileId), Is.True);
            Assert.That(stillGuid, Is.EqualTo(guid));
            Assert.That(stillFileId, Is.EqualTo(fileId));

            string metaPath = BuildFiles.RelativePath(path + ".meta");
            byte[] originalMeta = File.ReadAllBytes(metaPath);
            File.AppendAllText(metaPath, "\n");
            try
            {
                var changedMeta = Run("plan", false, false, success: false, directExport: WriteExport("changed"));
                Assert.That(changedMeta.Error, Does.Contain("recorded snapshot"));
            }
            finally
            {
                File.WriteAllBytes(metaPath, originalMeta);
            }
            var external = WriteExport("alternate", register: false);
            var failed = Run("plan", false, false, success: false, directExport: external);
            Assert.That(failed.Error, Does.Contain("registered component export"));
        }

        BuildResult Run(string operation, bool updateBase, bool pageOverride, bool success = true, bool changedSource = false,
            bool changedBorder = false, bool sliced = false, bool adoptMatching = false, bool resized = false,
            bool privateArtwork = false, ImageAsset directExport = null)
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
            if (sliced)
                main.DirectSha256 = new string('d', 64);
            main.Nodes = new[] { new Node { Id = "art", Kind = "image", AssetId = normal, Width = 24, Height = 24 } };
            if (privateArtwork)
                main.Nodes = main.Nodes.Append(new Node { Id = "decoration", Kind = "image", AssetId = normal, Width = 16, Height = 16 }).ToArray();
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
                DataRoot = dataRoot, ProjectFile = dataRoot + "/project.json", ResultPath = resultPath, AdoptMatching = adoptMatching,
                Targets = new[] { main, variant, page }, Fonts = new(),
                Controls = new Dictionary<string, Control> { [id + ".base"] = ControlMapping("base"), [id + ".variant"] = ControlMapping("variant") },
                Assets = new[] { "normal", "alternate", "changed" }.ToDictionary(key => id + "." + key, key => ImageMapping(key))
            };
            if (directExport != null)
                request.Assets[id + ".normal"] = directExport;
            if (resized)
            {
                request.Targets = new[] { main, variant };
                foreach (var control in request.Controls.Values)
                {
                    control.Width = 32;
                    control.Height = 28;
                }
                main.Width = variant.Width = 32;
                main.Height = variant.Height = 28;
                variant.DependencySha256 = new string('e', 64);
                main.DirectSha256 = new string('e', 64);
            }
            if (sliced)
                foreach (var asset in request.Assets.Values)
                    asset.Geometry = new JObject { ["mode"] = "nine_slice",
                        ["borders"] = new JObject { ["left"] = 2, ["bottom"] = 3, ["right"] = 4, ["top"] = 5 } };
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

        static Image Art(GameObject page, string node, string imageNode = "art") => page.transform.Find(PrefabGraph.NodeName(node) + "/" +
            PrefabGraph.InstanceName(node) + "/" + PrefabGraph.NodeName(imageNode)).GetComponent<Image>();

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
