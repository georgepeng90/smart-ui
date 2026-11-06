using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using TMPro;
using UnityEditor;
using UnityEngine;
using UnityEngine.UI;
using Object = UnityEngine.Object;

namespace SmartUI.UGUI.Editor
{
    internal sealed class PrefabGraph
    {
        // The colon separates managed object names from the Prefab root, whose name can come from its filename.
        const string NodePrefix = "SmartUI_Node:";
        static readonly Vector2 TopLeft = new(0, 1);
        internal static readonly Dictionary<string, string> ButtonStates = new()
        {
            ["highlighted"] = "m_HighlightedSprite", ["pressed"] = "m_PressedSprite",
            ["selected"] = "m_SelectedSprite", ["disabled"] = "m_DisabledSprite"
        };

        readonly BuildRequest request;
        readonly BuildResources resources;
        readonly Target target;
        readonly GameObject root;
        readonly Dictionary<string, GameObject> nodes = new();
        readonly Dictionary<string, GameObject> instances = new();
        readonly Dictionary<string, Dictionary<string, Component>> instanceSlots = new();

        internal PrefabGraph(BuildRequest request, BuildResources resources, Target target, GameObject root, bool create)
        {
            this.request = request;
            this.resources = resources;
            this.target = target;
            this.root = root;
            RequireSingle<RectTransform>(root);
            nodes.Add("root", root);
            if (target.Kind == "variant")
            {
                PrefabAssets.RequireConnection(root, request.Controls[target.BaseControlId].Path);
                return;
            }
            foreach (var node in target.Nodes)
            {
                var parent = nodes[node.ParentId ?? "root"].transform;
                string name = NodeName(node.Id);
                var item = create ? PrefabAssets.NewRect(name, root.scene, parent) : Child(parent, name);
                nodes.Add(node.Id, item);
                switch (node.Kind)
                {
                    case "image" when create:
                        item.AddComponent<Image>();
                        break;
                    case "text" when create:
                        item.AddComponent<TextMeshProUGUI>();
                        break;
                    case "control":
                        var control = request.Controls[node.ControlId];
                        GameObject instance;
                        if (create)
                        {
                            instance = PrefabAssets.Instantiate(RequireControl(node.ControlId), root.scene, item.transform);
                            instance.name = InstanceName(node.Id);
                            PrefabUtility.RecordPrefabInstancePropertyModifications(instance);
                        }
                        else
                        {
                            if (item.transform.childCount != 1)
                                throw new InvalidOperationException($"'{node.Id}' must contain one nested prefab instance.");
                            instance = Child(item.transform, InstanceName(node.Id));
                        }
                        PrefabAssets.RequireConnection(instance, control.Path);
                        instances.Add(node.Id, instance);
                        instanceSlots.Add(node.Id, ResolveControlSlots(instance, node.ControlId));
                        break;
                }
            }
            if (create && target.Button != null)
                root.AddComponent<Button>();
            if (create && target.Kind == "page")
                root.AddComponent<Image>();
        }

        internal static string NodeName(string id) => NodePrefix + id;
        internal static string InstanceName(string id) => "SmartUI_Instance:" + id;

        GameObject RequireControl(string id)
        {
            var control = request.Controls[id];
            var asset = PrefabAssets.RequirePrefab(control.Path, control.Guid);
            PrefabAssets.RequireClean(control.Path, asset);
            return asset;
        }

        static GameObject Child(Transform parent, string name)
        {
            var matches = parent.Cast<Transform>().Where(child => child.name == name).ToArray();
            if (matches.Length != 1)
                throw new InvalidOperationException($"'{parent.name}' must have a single child named '{name}'.");
            return matches[0].gameObject;
        }

        static T RequireSingle<T>(GameObject item) where T : Component
        {
            var matches = item.GetComponents<T>();
            if (matches.Length != 1)
                throw new InvalidOperationException($"'{item.name}' must have a single {typeof(T).Name} component.");
            return matches[0];
        }

        static Type SlotType(string type) => type switch
        {
            "rect" => typeof(RectTransform), "image" => typeof(Image), "text" => typeof(TextMeshProUGUI),
            "button" => typeof(Button), _ => throw new InvalidOperationException($"Unsupported slot component '{type}'.")
        };

        static Component SlotComponent(GameObject item, Slot slot)
        {
            var matches = item.GetComponents(SlotType(slot.Component));
            if (matches.Length != 1)
                throw new InvalidOperationException($"'{slot.NodeId}' must have a single '{slot.Component}' slot component.");
            return matches[0];
        }

        Dictionary<string, Component> ResolveControlSlots(GameObject scope, string controlId)
        {
            var control = request.Controls[controlId];
            var result = new Dictionary<string, Component>();
            foreach (var pair in control.Slots)
            {
                if (!control.SlotSources.TryGetValue(pair.Key, out var identity))
                    throw new InvalidOperationException($"'{controlId}' has no saved source identity for slot '{pair.Key}'.");
                var source = request.Controls[identity.ControlId];
                RequireControl(identity.ControlId);
                var component = PrefabAssets.ResolveComponent(scope, source.Path, source.Guid, identity.FileId);
                if (component.GetType() != SlotType(pair.Value.Component))
                    throw new InvalidOperationException($"'{controlId}' slot '{pair.Key}' has type '{component.GetType().Name}'.");
                result.Add(pair.Key, component);
            }
            return result;
        }

        internal JObject ReadIdentities(string guid, TargetResult result)
        {
            var identities = new JObject();
            long Add(string key, Object value)
            {
                var (assetGuid, fileId) = PrefabAssets.RequireIdentity(value);
                if (assetGuid != guid)
                    throw new InvalidOperationException($"'{target.Path}' object '{key}' has no persistent ID in the output Prefab.");
                identities.Add(key, fileId);
                return fileId;
            }
            if (target.Kind == "variant")
            {
                ResolveControlSlots(root, target.BaseControlId);
                var baseControl = request.Controls[target.BaseControlId];
                identities.Add("root/source", baseControl.Guid);
                foreach (var pair in baseControl.SlotSources)
                    identities.Add("inherited/" + pair.Key, new JArray(pair.Value.ControlId,
                        request.Controls[pair.Value.ControlId].Guid, pair.Value.FileId));
                result.SourceGuids = new[] { baseControl.Guid };
                return identities;
            }
            Add("root/object", root);
            Add("root/rect", RequireSingle<RectTransform>(root));
            if (target.Kind == "page")
                Add("root/background", RequireSingle<Image>(root));
            var scopeIds = new HashSet<long>();
            foreach (var node in target.Nodes)
            {
                var item = nodes[node.Id];
                Add(node.Id + "/object", item);
                long rectId = Add(node.Id + "/rect", RequireSingle<RectTransform>(item));
                if (node.Kind == "image")
                    Add(node.Id + "/image", RequireSingle<Image>(item));
                if (node.Kind == "text")
                    Add(node.Id + "/text", RequireSingle<TextMeshProUGUI>(item));
                if (node.Kind == "control")
                {
                    if (!scopeIds.Add(rectId))
                        throw new InvalidOperationException($"'{target.Path}' instances share wrapper file ID '{rectId}'.");
                    var slots = new Dictionary<string, SlotFileId>();
                    var control = request.Controls[node.ControlId];
                    foreach (var slot in instanceSlots[node.Id].Keys)
                    {
                        var source = control.SlotSources[slot];
                        var sourceGuid = request.Controls[source.ControlId].Guid;
                        identities.Add($"{node.Id}/slot/{slot}", new JArray(rectId, source.ControlId, sourceGuid, source.FileId));
                        slots.Add(slot, new SlotFileId { ScopeFileId = rectId, ControlId = source.ControlId, Guid = sourceGuid, FileId = source.FileId });
                    }
                    result.Instances.Add(node.Id, slots);
                }
            }
            if (target.Button != null)
                Add("root/button", RequireSingle<Button>(root));
            foreach (var pair in target.Slots)
                result.SlotBindings.Add(pair.Key, Add("slot/" + pair.Key, SlotComponent(nodes[pair.Value.NodeId], pair.Value)));
            result.SourceGuids = target.Nodes.Where(node => node.Kind == "control").Select(node => request.Controls[node.ControlId].Guid)
                .Distinct().OrderBy(guidValue => guidValue, StringComparer.Ordinal).ToArray();
            return identities;
        }

        internal ManagedProperties Properties(bool desired)
        {
            var properties = new ManagedProperties();
            if (target.Kind == "variant")
            {
                AddVariantProperties(properties, desired);
                return properties;
            }
            var buttonNode = target.Button?.TargetGraphic;
            RectFields(properties, "root", RequireSingle<RectTransform>(root), new Rect(0, 0, target.Width, target.Height));
            if (target.Kind == "page")
            {
                var background = RequireSingle<Image>(root);
                ColorUtility.TryParseHtmlString(target.Background, out var color);
                Own(properties, "root/background", background, "m_Color", color);
                Own(properties, "root/background", background, "m_Sprite", null);
                Own(properties, "root/background", background, "m_RaycastTarget", false);
            }
            var siblingCounts = new Dictionary<string, int>();
            foreach (var node in target.Nodes)
            {
                var item = nodes[node.Id];
                var rect = new Rect(node.X, node.Y, node.Width, node.Height);
                if (node.Kind == "image")
                    rect = BuildResources.ImageRect(resources.Image(node.AssetId), rect);
                RectFields(properties, node.Id, RequireSingle<RectTransform>(item), rect);
                string parent = node.ParentId ?? "root";
                siblingCounts.TryGetValue(parent, out int order);
                properties.SiblingOrder(node.Id + "/order", item.transform, order);
                siblingCounts[parent] = order + 1;
                switch (node.Kind)
                {
                    case "image":
                        var image = RequireSingle<Image>(item);
                        var imageAsset = resources.Image(node.AssetId);
                        Own(properties, node.Id, image, "m_Sprite", desired ? BuildResources.LoadSprite(imageAsset) : null);
                        Own(properties, node.Id, image, "m_Type", (int)(BuildResources.Sliced(imageAsset) ? Image.Type.Sliced : Image.Type.Simple));
                        Own(properties, node.Id, image, "m_RaycastTarget", node.Id == buttonNode);
                        break;
                    case "text":
                        AddText(properties, node, RequireSingle<TextMeshProUGUI>(item));
                        break;
                    case "control":
                        var instance = instances[node.Id];
                        RectFields(properties, node.Id + "/instance", RequireSingle<RectTransform>(instance), new Rect(), true);
                        AddParameters(properties, node, instance, desired);
                        break;
                }
            }
            if (target.Button != null)
                AddButton(properties, desired);
            return properties;
        }

        static void Own(ManagedProperties fields, string key, Object value, string path, object desired, bool localRef = false) =>
            fields.Own(key + "/" + path, value, path, desired, localRef);

        static void RectFields(ManagedProperties fields, string key, RectTransform value, Rect rect, bool stretch = false)
        {
            fields.RectLayout(key + "/rect", value, stretch ? Vector2.zero : TopLeft, stretch ? Vector2.one : TopLeft, TopLeft, rect);
        }

        void AddText(ManagedProperties fields, Node node, TextMeshProUGUI text)
        {
            Own(fields, node.Id, text, "m_text", node.Text);
            Own(fields, node.Id, text, "m_HorizontalAlignment", (int)(node.Align switch
            {
                "left" => HorizontalAlignmentOptions.Left, "center" => HorizontalAlignmentOptions.Center,
                "right" => HorizontalAlignmentOptions.Right, _ => throw new InvalidOperationException($"Unsupported text alignment '{node.Align}'.")
            }));
            Own(fields, node.Id, text, "m_VerticalAlignment", (int)VerticalAlignmentOptions.Middle);
            Own(fields, node.Id, text, "m_TextWrappingMode", (int)(node.Wrap == true ? TextWrappingModes.Normal : TextWrappingModes.NoWrap));
            Own(fields, node.Id, text, "m_overflowMode", (int)TextOverflowModes.Overflow);
            Own(fields, node.Id, text, "m_isRichText", false);
            Own(fields, node.Id, text, "m_parseCtrlCharacters", false);
            Own(fields, node.Id, text, "m_enableAutoSizing", false);
            Own(fields, node.Id, text, "m_RaycastTarget", false);
            foreach (var field in TextStyleValues(node.TextStyleId, false))
                Own(fields, node.Id, text, field.Key, field.Value);
        }

        Dictionary<string, object> TextStyleValues(string id, bool channels)
        {
            var (style, font, material) = resources.Font(id);
            if (!ColorUtility.TryParseHtmlString(style.Fill.Color, out var color) || style.Fill.Opacity is < 0 or > 1)
                throw new InvalidOperationException($"'{id}' has an invalid TMP fill.");
            color.a *= style.Fill.Opacity;
            float emScale = style.FontSize * 0.01f;
            float fontLine = font.faceInfo.lineHeight * style.FontSize / font.faceInfo.pointSize * font.faceInfo.scale;
            var values = new Dictionary<string, object>
            {
                ["m_fontAsset"] = font, ["m_sharedMaterial"] = material, ["m_fontSize"] = style.FontSize,
                ["m_fontSizeBase"] = style.FontSize, ["m_lineSpacing"] = (style.LineHeight - fontLine) / emScale,
                ["m_characterSpacing"] = style.LetterSpacing / emScale
            };
            if (channels)
            {
                values.Add("m_fontColor.r", color.r);
                values.Add("m_fontColor.g", color.g);
                values.Add("m_fontColor.b", color.b);
                values.Add("m_fontColor.a", color.a);
            }
            else
                values.Add("m_fontColor", color);
            return values;
        }

        void AddButton(ManagedProperties fields, bool desired)
        {
            var button = RequireSingle<Button>(root);
            var image = RequireSingle<Image>(nodes[target.Button.TargetGraphic]);
            Own(fields, "button", button, "m_TargetGraphic", image, true);
            Own(fields, "button", button, "m_Transition", (int)(target.Button.States.Keys.Any(key => key != "normal")
                ? Selectable.Transition.SpriteSwap : Selectable.Transition.None));
            Own(fields, "button", button, "m_Interactable", true);
            foreach (var state in ButtonStates)
                Own(fields, "button", button, "m_SpriteState." + state.Value,
                    desired && target.Button.States.TryGetValue(state.Key, out var id) ? BuildResources.LoadSprite(resources.Image(id)) : null);
        }

        void AddParameters(ManagedProperties fields, Node node, GameObject instance, bool desired)
        {
            var control = request.Controls[node.ControlId];
            foreach (var pair in control.Parameters)
            {
                var parameter = pair.Value;
                var component = instanceSlots[node.Id][parameter.SlotId];
                bool explicitValue = node.Parameters.TryGetValue(pair.Key, out var token);
                string property = OverridePath(component, parameter.Property);
                object value = !desired || !explicitValue ? null : OverrideValue(component, parameter.Property, token);
                fields.Local(node.Id + "/parameter/" + pair.Key, instance, component, control.Path, property, explicitValue, value);
            }
        }

        void AddVariantProperties(ManagedProperties fields, bool desired)
        {
            string sourcePath = request.Controls[target.BaseControlId].Path;
            // A variant can override only generated image and text nodes in its base hierarchy.
            var candidates = root.GetComponentsInChildren<Component>(true)
                .Where(component => component is Image or TextMeshProUGUI)
                .Where(component => component.gameObject.name.StartsWith(NodePrefix, StringComparison.Ordinal) &&
                    PrefabUtility.GetNearestPrefabInstanceRoot(component) == root);
            foreach (var component in candidates)
            {
                string id = component.gameObject.name.Substring(NodePrefix.Length);
                string simpleProperty = component is Image ? "asset_id" : "text";
                var simple = target.Overrides.SingleOrDefault(entry => entry.NodeId == id && entry.Property == simpleProperty);
                string path = OverridePath(component, simpleProperty);
                object value = desired && simple != null ? OverrideValue(component, simpleProperty, simple.Value) : null;
                fields.Local(id + "/override/" + path, root, component, sourcePath, path, simple != null, value);
                if (component is not TextMeshProUGUI)
                    continue;
                var style = target.Overrides.SingleOrDefault(entry => entry.NodeId == id && entry.Property == "text_style_id");
                var styles = desired && style != null ? TextStyleValues((string)style.Value, true) : null;
                foreach (string stylePath in new[] { "m_fontAsset", "m_sharedMaterial", "m_fontSize", "m_fontSizeBase", "m_lineSpacing",
                             "m_characterSpacing", "m_fontColor.r", "m_fontColor.g", "m_fontColor.b", "m_fontColor.a" })
                {
                    fields.Local(id + "/override/" + stylePath, root, component, sourcePath, stylePath, style != null, styles?[stylePath]);
                }
            }
            foreach (var entry in target.Overrides)
            {
                if (candidates.Count(component => component.gameObject.name == NodeName(entry.NodeId) &&
                        (component is Image ? entry.Property == "asset_id" : entry.Property is "text" or "text_style_id")) != 1)
                    throw new InvalidOperationException($"'{target.Id}' cannot locate one override target '{entry.NodeId}.{entry.Property}'.");
            }
        }

        static string OverridePath(Component component, string property) => (component, property) switch
        {
            (TextMeshProUGUI, "text") => "m_text", (Image, "asset_id") => "m_Sprite",
            _ => throw new InvalidOperationException($"'{component.name}' does not support instance property '{property}'.")
        };

        object OverrideValue(Component component, string property, JToken token)
        {
            if (token.Type != JTokenType.String)
                throw new InvalidOperationException($"'{component.name}.{property}' requires a string value.");
            if (property == "text")
                return (string)token;
            var image = (Image)component;
            var replacement = resources.Image((string)token);
            var source = (Image)PrefabUtility.GetCorrespondingObjectFromSource(image);
            if (source == null)
                throw new InvalidOperationException($"'{component.name}' has no source Image for the Sprite override.");
            resources.RequireMatchingImageOverride(source, replacement);
            return BuildResources.LoadSprite(replacement);
        }

        internal void RequireTextFits()
        {
            foreach (var label in root.GetComponentsInChildren<TextMeshProUGUI>(true))
            {
                if (label.font == null || label.fontSharedMaterial == null)
                    throw new InvalidOperationException($"'{label.name}' requires a TMP font and material.");
                if (string.IsNullOrEmpty(label.text))
                    continue;
                if (!label.font.HasCharacters(label.text.Replace("\n", ""), out uint[] missing, false, false))
                    throw new InvalidOperationException($"'{label.name}' TMP font does not have imported glyphs {string.Join(", ", missing.Select(glyph => $"U+{glyph:X4}"))}.");
                var size = label.rectTransform.rect.size;
                var width = label.textWrappingMode == TextWrappingModes.NoWrap ? Mathf.Infinity : size.x;
                var preferred = label.GetPreferredValues(label.text, width, Mathf.Infinity);
                if (preferred.x > size.x + 0.1f || preferred.y > size.y + 0.1f)
                    throw new InvalidOperationException($"'{label.name}' requires {preferred} for text '{label.text}' but has {size}.");
            }
        }
    }
}
