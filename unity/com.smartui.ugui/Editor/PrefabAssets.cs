using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace SmartUI.UGUI.Editor
{
    internal static class PrefabAssets
    {
        internal static string ProjectPath => Path.GetDirectoryName(Application.dataPath);

        internal static string PhysicalPath(string assetPath) => Path.Combine(ProjectPath, assetPath);

        internal static void RequireAssetPath(string path, bool output = false)
        {
            if (string.IsNullOrEmpty(path) || path.Contains('\\') || path.Contains(':') ||
                path.Split('/').Any(segment => segment.Length == 0 || segment == "." || segment == ".." ||
                    segment.EndsWith(".", StringComparison.Ordinal) || segment.Trim() != segment ||
                    segment.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0) ||
                (!path.StartsWith("Assets/", StringComparison.Ordinal) &&
                    (output || !path.StartsWith("Packages/", StringComparison.Ordinal))))
                throw new InvalidOperationException($"Invalid Unity asset path '{path}'.");

            if (output && !path.EndsWith(".prefab", StringComparison.Ordinal))
                throw new InvalidOperationException($"Prefab output '{path}' must end with .prefab.");
        }

        internal static string FileHash(string path)
        {
            if (!File.Exists(path))
                return null;
            using var stream = File.OpenRead(path);
            using var hash = SHA256.Create();
            return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        internal static GameObject RequirePrefab(string path, string expectedGuid)
        {
            RequireAssetPath(path);
            var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (asset == null || !PrefabUtility.IsPartOfPrefabAsset(asset) ||
                !string.Equals(AssetDatabase.AssetPathToGUID(path), expectedGuid, StringComparison.Ordinal))
                throw new InvalidOperationException($"Prefab '{path}' must resolve to GUID '{expectedGuid}'.");
            return asset;
        }

        internal static void RequireClean(string assetPath, Object asset)
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && stage.assetPath == assetPath && stage.scene.isDirty)
                throw new InvalidOperationException($"Prefab Stage has unsaved changes to '{assetPath}'.");
            if (asset != null && EditorUtility.IsDirty(asset))
                throw new InvalidOperationException($"Editor has unsaved changes to '{assetPath}'.");
            if (asset is GameObject root && root.GetComponentsInChildren<Component>(true).Any(component =>
                    component != null && (EditorUtility.IsDirty(component) || EditorUtility.IsDirty(component.gameObject))))
                throw new InvalidOperationException($"Editor has unsaved changes inside '{assetPath}'.");
        }

        internal static void EnsureOutputDirectory(string path)
        {
            var parent = path.Substring(0, path.LastIndexOf('/'));
            if (AssetDatabase.IsValidFolder(parent))
                return;

            EnsureOutputDirectory(parent);
            int separator = parent.LastIndexOf('/');
            if (string.IsNullOrEmpty(AssetDatabase.CreateFolder(parent.Substring(0, separator), parent.Substring(separator + 1))) ||
                !AssetDatabase.IsValidFolder(parent))
                throw new IOException($"Unity could not create output folder '{parent}'.");
        }

        internal static GameObject NewRect(string name, Scene scene, Transform parent = null)
        {
            var gameObject = new GameObject(name, typeof(RectTransform));
            SceneManager.MoveGameObjectToScene(gameObject, scene);
            if (parent != null)
                gameObject.transform.SetParent(parent, false);
            return gameObject;
        }

        internal static GameObject Instantiate(GameObject asset, Scene scene, Transform parent = null)
        {
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset, scene);
            if (instance == null)
                throw new InvalidOperationException($"Unity could not instantiate '{AssetDatabase.GetAssetPath(asset)}'.");
            if (parent != null)
                instance.transform.SetParent(parent, false);
            return instance;
        }

        internal static (string guid, long fileId) RequireIdentity(Object value)
        {
            if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long fileId) ||
                string.IsNullOrEmpty(guid) || fileId == 0)
                throw new InvalidOperationException($"Could not identify saved Unity object '{value.name}'.");
            return (guid, fileId);
        }

        internal static Component ResolveComponent(GameObject scope, string sourcePath, string sourceGuid, long fileId)
        {
            var matches = new List<Component>();
            foreach (var component in scope.GetComponentsInChildren<Component>(true))
            {
                if (component == null)
                    throw new InvalidOperationException($"'{scope.name}' has a missing script.");

                var source = AssetDatabase.GetAssetPath(component) == sourcePath
                    ? component
                    : PrefabUtility.GetCorrespondingObjectFromSourceAtPath(component, sourcePath);
                if (source != null && RequireIdentity(source) == (sourceGuid, fileId))
                    matches.Add(component);
            }
            if (matches.Count != 1)
                throw new InvalidOperationException($"'{scope.name}' contains {matches.Count} matches for slot source '{sourcePath}' and file ID '{fileId}'.");
            return matches[0];
        }

        internal static void RequireConnection(GameObject instance, string path)
        {
            var source = PrefabUtility.GetCorrespondingObjectFromSource(instance);
            if (source == null || AssetDatabase.GetAssetPath(source) != path)
                throw new InvalidOperationException($"'{instance.name}' must remain connected to '{path}'.");
        }
    }
}
