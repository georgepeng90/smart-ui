using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace SmartUI.UGUI.Editor
{
    public static class PrefabBuild
    {
        public static string Execute(string requestPath)
        {
            if (!Path.IsPathFullyQualified(requestPath))
                throw new ArgumentException("SmartUI requires an absolute request path.", nameof(requestPath));
            var fullPath = Path.GetFullPath(requestPath);
            var relative = Path.GetRelativePath(PrefabAssets.ProjectPath, fullPath).Replace('\\', '/');
            var match = Regex.Match(relative, @"\Asmart-ui/unity/operations/([0-9a-f]{32})/request\.json\z");
            if (!match.Success || !string.Equals(fullPath, Path.GetFullPath(BuildFiles.RelativePath(relative)),
                    Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
                throw new ArgumentException("SmartUI request must be in the current project's operation directory.", nameof(requestPath));

            var bytes = File.ReadAllBytes(fullPath);
            string requestSha;
            using (var sha = SHA256.Create())
                requestSha = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            var request = BuildFiles.Deserialize<BuildRequest>(new UTF8Encoding(false, true).GetString(bytes));
            string id = match.Groups[1].Value;
            string resultPath = $"smart-ui/unity/operations/{id}/result.json";
            if (request.SchemaVersion != 1 || request.RequestId != id || request.ResultPath != resultPath)
                throw new InvalidOperationException($"SmartUI request '{relative}' has an invalid schema, identity or result path.");

            var fullResult = BuildFiles.RelativePath(resultPath);
            var previous = BuildFiles.Read<BuildResult>(fullResult);
            if (previous != null)
            {
                if (previous.SchemaVersion != 1 || previous.RequestId != id || previous.RequestSha256 != requestSha)
                    throw new InvalidOperationException($"Existing receipt for '{id}' does not match the request.");
                return previous.Status;
            }

            var result = new BuildResult { RequestId = id, RequestSha256 = requestSha, Status = "success" };
            new BuildExecution(request).Run(result);
            BuildFiles.Write(fullResult, result);
            return result.Status;
        }
    }

    internal sealed class BuildExecution
    {
        readonly BuildRequest request;
        readonly BuildResources resources;
        bool IsPlan => request.Operation == "plan";
        bool IsVerify => request.Operation == "verify";

        internal BuildExecution(BuildRequest request)
        {
            this.request = request;
            resources = new BuildResources(request);
        }

        internal void Run(BuildResult result)
        {
            result.Targets = (request.Targets ?? Array.Empty<Target>()).Select(target => new TargetResult
            {
                Id = target?.Id, Kind = target?.Kind, Path = target?.Path
            }).ToArray();
            try
            {
                ValidateRequest();
                resources.ValidateImages(IsVerify || request.AdoptMatching);
                foreach (var id in request.Fonts.Keys)
                    resources.Font(id);
                var states = new TargetState[request.Targets.Length];
                var actions = new string[request.Targets.Length];
                for (int i = 0; i < request.Targets.Length; i++)
                {
                    var item = result.Targets[i];
                    try
                    {
                        (states[i], actions[i]) = Preflight(request.Targets[i], item);
                        if (IsPlan)
                            item.Status = actions[i];
                    }
                    catch (Exception error)
                    {
                        Fail(item, error.Message);
                        throw;
                    }
                }
                if (IsPlan)
                    return;

                if (!IsVerify && !request.AdoptMatching)
                    resources.ImportImages();
                for (int i = 0; i < request.Targets.Length; i++)
                {
                    var item = result.Targets[i];
                    try
                    {
                        ExecuteTarget(request.Targets[i], states[i], actions[i], item);
                    }
                    catch (Exception error)
                    {
                        Fail(item, error.Message);
                        throw;
                    }
                }
            }
            catch (Exception error)
            {
                result.Status = "failed";
                result.Error = error.Message;
                foreach (var item in result.Targets.Where(item => item.Status == null))
                    Fail(item, $"Target was not executed. {error.Message}");
            }
        }

        void Fail(TargetResult item, string error)
        {
            item.Status = IsPlan ? "conflict" : "failed";
            item.Error = error;
            if (item.Path == null)
                return;
            try
            {
                PrefabAssets.RequireAssetPath(item.Path, true);
                UpdateFileResult(item);
            }
            catch (Exception inspectionError)
            {
                item.Error += $" Could not inspect the target files: {inspectionError.Message}";
            }
        }

        void ValidateRequest()
        {
            if (!(request.Operation is "plan" or "apply" or "verify") || request.Targets == null ||
                request.Targets.Length == 0 || request.Assets == null || request.Fonts == null || request.Controls == null)
                throw new InvalidOperationException("SmartUI request has an invalid operation or missing collections.");
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                throw new InvalidOperationException("SmartUI requires an idle Unity Editor in Edit Mode.");
            if (EditorSettings.serializationMode != SerializationMode.ForceText)
                throw new InvalidOperationException("SmartUI requires Force Text asset serialization.");
            var project = BuildFiles.Read<JObject>(BuildFiles.RelativePath("smart-ui/project.json"));
            if (project == null || (string)project["project_id"] != request.ProjectId)
                throw new InvalidOperationException($"Current SmartUI project does not match '{request.ProjectId}'.");

            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var keys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var pair in request.Assets)
            {
                BuildFiles.RequireId(pair.Key, "image");
                if (pair.Value.OutputPath != null && !paths.Add(pair.Value.OutputPath))
                    throw new InvalidOperationException($"Duplicate output '{pair.Value.OutputPath}'.");
            }

            var targetControls = request.Targets.Where(target => target.Kind != "page").Select(target => target.Id).ToHashSet();
            foreach (var pair in request.Controls)
            {
                BuildFiles.RequireId(pair.Key, "control");
                var control = pair.Value;
                PrefabAssets.RequireAssetPath(control.Path, true);
                if (control.Slots == null || control.SlotSources == null || control.Parameters == null)
                    throw new InvalidOperationException($"'{pair.Key}' has an incomplete control mapping.");
                if (control.Guid != null)
                {
                    var asset = PrefabAssets.RequirePrefab(control.Path, control.Guid);
                    PrefabAssets.RequireClean(control.Path, asset);
                    if (!targetControls.Contains(pair.Key) && (control.Sha256 != null || control.MetaSha256 != null))
                        BuildFiles.RequireSnapshot(control.Path, BuildFiles.Snapshot(control.Path), new FileSnapshot
                            { Sha256 = control.Sha256, MetaSha256 = control.MetaSha256 });
                }
                else if (!targetControls.Contains(pair.Key))
                    throw new InvalidOperationException($"'{pair.Key}' has no registered Prefab GUID.");
            }
            var built = new HashSet<string>();
            foreach (var target in request.Targets)
            {
                BuildFiles.RequireId(target.Id, "target");
                PrefabAssets.RequireAssetPath(target.Path, true);
                if (!(target.Kind is "base" or "page" or "variant") || !keys.Add(target.Kind + "/" + target.Id) ||
                    !paths.Add(target.Path) || target.Nodes == null || target.Overrides == null || target.Slots == null ||
                    target.Expected == null || !Positive(target.Width) || !Positive(target.Height))
                    throw new InvalidOperationException($"'{target.Id}' has an invalid or duplicate target declaration.");
                if (target.Kind != "page" && request.Controls[target.Id].Path != target.Path)
                    throw new InvalidOperationException($"'{target.Id}' does not match its control mapping path.");
                if (target.Kind == "page" ? target.Background == null || !Regex.IsMatch(target.Background, @"\A#[0-9a-fA-F]{6}\z") : target.Background != null)
                    throw new InvalidOperationException($"'{target.Id}' requires #RRGGBB background on page targets only.");
                if (target.Kind == "variant")
                {
                    CheckDependency(target.BaseControlId, target.Id);
                    if (target.Nodes.Length != 0 || target.Button != null)
                        throw new InvalidOperationException($"Variant '{target.Id}' must inherit its structure and Button.");
                }
                else if (target.BaseControlId != null || target.Overrides.Length != 0)
                    throw new InvalidOperationException($"'{target.Id}' has overrides reserved for variants.");
                ValidateNodes(target, CheckDependency);
                if (target.Kind != "page")
                    built.Add(target.Id);
            }

            void CheckDependency(string id, string owner)
            {
                if (id == null || !request.Controls.ContainsKey(id) || (targetControls.Contains(id) && !built.Contains(id)))
                    throw new InvalidOperationException($"'{owner}' must follow its dependency '{id}' in the build order.");
            }
        }

        static bool Positive(float value) => value > 0 && !float.IsInfinity(value);

        void ValidateNodes(Target target, Action<string, string> requireDependency)
        {
            var nodes = new Dictionary<string, Node>(StringComparer.OrdinalIgnoreCase);
            foreach (var node in target.Nodes)
            {
                BuildFiles.RequireId(node.Id, "node");
                if (node.Id.Equals("root", StringComparison.OrdinalIgnoreCase) ||
                    (node.ParentId != null && (!nodes.TryGetValue(node.ParentId, out var parent) || parent.Kind != "group")) ||
                    !nodes.TryAdd(node.Id, node) || !Positive(node.Width) || !Positive(node.Height) ||
                    float.IsNaN(node.X) || float.IsNaN(node.Y) || float.IsInfinity(node.X) || float.IsInfinity(node.Y))
                    throw new InvalidOperationException($"'{target.Id}' has an invalid node '{node.Id}', parent or rectangle.");

                if ((node.AnchorMin == null) != (node.AnchorMax == null) ||
                    !NormalizedPoint(node.AnchorMin) || !NormalizedPoint(node.AnchorMax) ||
                    !NormalizedPoint(node.Pivot) ||
                    (node.AnchorMin != null &&
                     (node.AnchorMin[0] > node.AnchorMax[0] || node.AnchorMin[1] > node.AnchorMax[1])))
                    throw new InvalidOperationException($"'{target.Id}' has invalid anchors or pivot on '{node.Id}'.");

                switch (node.Kind)
                {
                    case "group": break;
                    case "image":
                        BuildResources.ImageRect(resources.Image(node.AssetId), NodeRect(node));
                        break;
                    case "text":
                        if (node.Text == null || node.Wrap == null || !(node.Align is "left" or "center" or "right") || node.Overflow != "error")
                            throw new InvalidOperationException($"'{node.Id}' requires fixed TMP text with overflow 'error'.");
                        resources.Font(node.TextStyleId);
                        break;
                    case "control":
                        requireDependency(node.ControlId, target.Id);
                        var control = request.Controls[node.ControlId];
                        if (node.Width != control.Width || node.Height != control.Height || node.Parameters == null ||
                            node.Parameters.Keys.Any(id => !control.Parameters.ContainsKey(id)))
                            throw new InvalidOperationException($"'{node.Id}' has a size or parameter incompatible with '{node.ControlId}'.");
                        foreach (var pair in control.Parameters)
                        {
                            var parameter = pair.Value;
                            if (!control.Slots.TryGetValue(parameter.SlotId, out var slot) ||
                                !((slot.Component == "text" && parameter.Property == "text" && parameter.ValueType == "string") ||
                                  (slot.Component == "image" && parameter.Property == "asset_id" && parameter.ValueType == "asset_id")))
                                throw new InvalidOperationException($"'{node.ControlId}' has unsupported parameter '{pair.Key}'.");
                            if (!node.Parameters.TryGetValue(pair.Key, out var value))
                            {
                                if (parameter.Required)
                                    throw new InvalidOperationException($"'{node.Id}' is missing required parameter '{pair.Key}'.");
                                continue;
                            }
                            if (value.Type != JTokenType.String)
                                throw new InvalidOperationException($"'{node.Id}.{pair.Key}' must be a string.");
                            if (parameter.Property == "asset_id")
                                resources.Image((string)value);
                        }
                        break;
                    default: throw new InvalidOperationException($"'{node.Id}' uses unsupported node type '{node.Kind}'.");
                }
            }
            foreach (var change in target.Overrides)
            {
                if (change.Value?.Type != JTokenType.String || !(change.Property is "text" or "asset_id" or "text_style_id"))
                    throw new InvalidOperationException($"'{target.Id}' has an unsupported variant override.");
                if (change.Property == "asset_id")
                    resources.Image((string)change.Value);
                if (change.Property == "text_style_id")
                    resources.Font((string)change.Value);
            }
            if (target.Button != null)
            {
                var button = target.Button;
                if (target.Kind != "base" || !nodes.TryGetValue(button.TargetGraphic, out var image) || image.Kind != "image" || button.States == null)
                    throw new InvalidOperationException($"'{target.Id}' Button requires an Image target in its own hierarchy.");
                foreach (var state in button.States)
                {
                    if (state.Key == "normal" ? state.Value != image.AssetId : !PrefabGraph.ButtonStates.ContainsKey(state.Key))
                        throw new InvalidOperationException($"'{target.Id}' has an unsupported Button state or normal Sprite '{state.Key}'.");
                    resources.RequireSwapGeometry(resources.Image(image.AssetId), resources.Image(state.Value), NodeRect(image));
                }
            }
        }

        static bool NormalizedPoint(float[] value) =>
            value == null || value.Length == 2 && value.All(axis => axis >= 0 && axis <= 1);

        static Rect NodeRect(Node node) => new(node.X, node.Y, node.Width, node.Height);

        (TargetState state, string action) Preflight(Target target, TargetResult result)
        {
            var statePath = BuildFiles.StatePath(target.Kind, target.Id);
            var files = BuildFiles.Snapshot(target.Path);
            BuildFiles.RequireSnapshot(target.Path, files, new FileSnapshot
                { Sha256 = target.Expected.PrefabSha256, MetaSha256 = target.Expected.MetaSha256 });
            if (PrefabAssets.FileHash(statePath) != target.Expected.BaselineSha256)
                throw new InvalidOperationException($"'{target.Path}' baseline changed after the request was prepared.");
            BuildFiles.RequireNoPending(statePath);
            var state = BuildFiles.Read<TargetState>(statePath);
            if (state == null)
            {
                if (request.AdoptMatching)
                    throw new InvalidOperationException($"'{target.Path}' has no baseline to adopt.");
                if (files.Sha256 != null || files.MetaSha256 != null)
                    throw new InvalidOperationException($"'{target.Path}' already exists without a SmartUI baseline.");
                if (IsVerify)
                    throw new InvalidOperationException($"'{target.Path}' has not been created.");
                return (null, "create");
            }
            if (state.ProjectId != request.ProjectId || state.Path != target.Path || files.Sha256 == null || files.MetaSha256 == null)
                throw new InvalidOperationException($"'{target.Path}' does not match its SmartUI baseline.");
            RequireSameStructure(target, state.Declaration);
            if (target.Kind != "page")
                UpdateControl(target, state.Guid, state.Identities);
            var old = Inspect(target, state.Guid, result, false, state.Managed);
            ManagedProperties.RequireEqual(old.identities, state.Identities, target.Path + " identity");
            if (request.AdoptMatching && request.Operation == "apply")
            {
                Inspect(target, state.Guid, result, true);
                return (state, "verify");
            }
            ManagedProperties.RequireEqual(old.managed, state.Managed, target.Path);
            if (state.DirectSha256 != target.DirectSha256)
            {
                if (IsVerify)
                    throw new InvalidOperationException($"'{target.Path}' has unapplied changes in its own declaration.");
                BuildFiles.RequireWritable(target.Path);
                return (state, "update");
            }
            return (state, state.DependencySha256 != target.DependencySha256 ||
                state.Files.Sha256 != files.Sha256 || state.Files.MetaSha256 != files.MetaSha256 ? "verify" : "keep");
        }

        static void RequireSameStructure(Target target, Target old)
        {
            var currentNodes = target.Nodes.Select(node => (node.Id, node.Kind, node.ParentId, node.ControlId)).ToHashSet();
            if (!currentNodes.SetEquals(old.Nodes.Select(node => (node.Id, node.Kind, node.ParentId, node.ControlId))) ||
                target.BaseControlId != old.BaseControlId || target.Button?.TargetGraphic != old.Button?.TargetGraphic ||
                !target.Slots.Select(pair => (pair.Key, pair.Value.NodeId, pair.Value.Component))
                    .ToHashSet().SetEquals(old.Slots.Select(pair => (pair.Key, pair.Value.NodeId, pair.Value.Component))))
                throw new InvalidOperationException($"'{target.Id}' changed its hierarchy, control source, Button target or public slots. It needs a separate migration.");
        }

        void ExecuteTarget(Target target, TargetState previous, string action, TargetResult result)
        {
            var statePath = BuildFiles.StatePath(target.Kind, target.Id);
            bool write = false;
            if (!IsVerify && (action == "create" || action == "update"))
                write = Save(target, previous, statePath);
            UpdateFileResult(result);
            if (previous != null && result.Guid != previous.Guid)
                throw new InvalidOperationException($"'{target.Path}' changed its Prefab GUID.");
            var found = Inspect(target, result.Guid, result, true);
            if (previous != null)
                ManagedProperties.RequireEqual(found.identities, previous.Identities, target.Path + " identity");
            if (target.Kind != "page")
                UpdateControl(target, result.Guid, found.identities);
            if (!IsVerify && (write || action != "keep"))
            {
                target.Expected = null;
                var state = new TargetState
                {
                    ProjectId = request.ProjectId, Path = target.Path, Guid = result.Guid,
                    DirectSha256 = target.DirectSha256, DependencySha256 = target.DependencySha256,
                    Declaration = target, Managed = found.managed, Identities = found.identities,
                    Files = new FileSnapshot { Sha256 = result.Sha256, MetaSha256 = result.MetaSha256 }
                };
                BuildFiles.Complete(statePath, state);
            }
            result.Status = IsVerify || action == "verify" || (action == "update" && !write) ? "verified"
                : action == "create" ? "created" : action == "update" ? "updated" : "unchanged";
        }

        bool Save(Target target, TargetState previous, string statePath)
        {
            bool create = previous == null;
            var scene = create ? EditorSceneManager.NewPreviewScene() : default;
            GameObject root = null;
            try
            {
                if (!create)
                    root = PrefabUtility.LoadPrefabContents(target.Path);
                else if (target.Kind == "variant")
                {
                    var source = request.Controls[target.BaseControlId];
                    root = PrefabAssets.Instantiate(PrefabAssets.RequirePrefab(source.Path, source.Guid), scene);
                    root.name = Path.GetFileNameWithoutExtension(target.Path);
                    PrefabUtility.RecordPrefabInstancePropertyModifications(root);
                }
                else
                {
                    root = PrefabAssets.NewRect(Path.GetFileNameWithoutExtension(target.Path), scene);
                    root.SetActive(false);
                }
                var graph = new PrefabGraph(request, resources, target, root, create);
                var properties = graph.Properties(true);
                var desired = properties.Desired();
                if (!create && JToken.DeepEquals(properties.Snapshot(), desired))
                    return false;
                properties.Apply();
                if (create && target.Kind != "variant")
                    root.SetActive(true);
                ManagedProperties.RequireEqual(properties.Snapshot(), desired, target.Path);
                properties.RequireEffectiveOverrides();
                graph.RequireTextFits();
                BuildFiles.RequireWritable(target.Path);
                BuildFiles.MarkPending(statePath, target.Path);
                PrefabAssets.EnsureOutputDirectory(target.Path);
                PrefabUtility.SaveAsPrefabAsset(root, target.Path, out bool saved);
                if (!saved)
                    throw new IOException($"Unity did not report a successful save for '{target.Path}'.");
                return true;
            }
            finally
            {
                if (create)
                    EditorSceneManager.ClosePreviewScene(scene);
                else if (root != null)
                    PrefabUtility.UnloadPrefabContents(root);
            }
        }

        (JObject managed, JObject identities) Inspect(Target target, string guid, TargetResult result,
            bool verifyDesired, JObject baseline = null)
        {
            var asset = PrefabAssets.RequirePrefab(target.Path, guid);
            PrefabAssets.RequireClean(target.Path, asset);
            var expectedType = target.Kind == "variant" ? PrefabAssetType.Variant : PrefabAssetType.Regular;
            if (PrefabUtility.GetPrefabAssetType(asset) != expectedType)
                throw new InvalidOperationException($"'{target.Path}' must remain a {expectedType} Prefab.");
            result.SlotBindings.Clear();
            result.Instances.Clear();
            var saved = new PrefabGraph(request, resources, target, asset, false);
            var identities = saved.ReadIdentities(guid, result);
            string text = File.ReadAllText(BuildFiles.RelativePath(target.Path));
            var sources = Regex.Matches(text, @"(?m)^  m_SourcePrefab: \{fileID: -?[0-9]+, guid: ([0-9a-f]{32}), type: 3\}\r?$")
                .Cast<Match>().Select(match => match.Groups[1].Value).ToHashSet();
            if (!text.StartsWith("%YAML 1.1", StringComparison.Ordinal) || !sources.SetEquals(result.SourceGuids))
                throw new InvalidOperationException($"'{target.Path}' has unexpected serialized Prefab sources.");
            var loaded = PrefabUtility.LoadPrefabContents(target.Path);
            try
            {
                var graph = new PrefabGraph(request, resources, target, loaded, false);
                var properties = graph.Properties(verifyDesired);
                var managed = properties.Snapshot();
                if (baseline != null && !JToken.DeepEquals(baseline, managed))
                {
                    var desired = graph.Properties(true).Desired();
                    result.ManagedDifferences = new JObject();
                    foreach (string key in baseline.Properties().Select(item => item.Name)
                                 .Union(managed.Properties().Select(item => item.Name)))
                    {
                        if (JToken.DeepEquals(baseline[key], managed[key]))
                            continue;
                        result.ManagedDifferences[key] = new JObject
                        {
                            ["baseline"] = baseline[key]?.DeepClone(),
                            ["unity"] = managed[key]?.DeepClone(),
                            ["design"] = desired[key]?.DeepClone()
                        };
                    }
                }
                if (verifyDesired)
                {
                    ManagedProperties.RequireEqual(managed, properties.Desired(), target.Path);
                    properties.RequireEffectiveOverrides();
                    graph.RequireTextFits();
                }
                UpdateFileResult(result);
                return (managed, identities);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(loaded);
            }
        }

        void UpdateControl(Target target, string guid, JObject identities)
        {
            var control = request.Controls[target.Id];
            if (control.Guid != null && control.Guid != guid)
                throw new InvalidOperationException($"'{target.Id}' changed its Prefab GUID.");
            control.Guid = guid;
            control.SlotSources = target.Kind == "variant" ? new Dictionary<string, SlotSource>(request.Controls[target.BaseControlId].SlotSources)
                : target.Slots.ToDictionary(pair => pair.Key, pair => new SlotSource
                    { ControlId = target.Id, FileId = (long)identities["slot/" + pair.Key] });
        }

        static void UpdateFileResult(TargetResult result)
        {
            var snapshot = BuildFiles.Snapshot(result.Path);
            result.Sha256 = snapshot.Sha256;
            result.MetaSha256 = snapshot.MetaSha256;
            result.Guid = snapshot.MetaSha256 == null ? null : AssetDatabase.AssetPathToGUID(result.Path);
        }
    }
}
