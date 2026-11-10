using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace SmartUI.UGUI.Editor
{
    internal sealed class ManagedProperties
    {
        readonly SortedDictionary<string, (Func<JToken> read, JToken desired, Action write)> fields = new(StringComparer.Ordinal);
        readonly Dictionary<GameObject, List<OverrideField>> overrides = new();

        sealed class OverrideField
        {
            internal Object Source, Original, InstanceObject;
            internal string Path, Key;
            internal bool Explicit;
            internal object Value;

            internal bool Matches(PropertyModification change) => change.propertyPath == Path &&
                change.target != null && (change.target == Source || change.target == Original);

            internal PropertyModification Modification() => new()
            {
                target = Source, propertyPath = Path, objectReference = Value as Object,
                value = Value is Object ? null : Convert.ToString(Value, CultureInfo.InvariantCulture)
            };
        }

        internal void Own(string key, Object target, string path, object desired, bool localReference = false)
        {
            var serialized = new SerializedObject(target);
            SerializedProperty Property()
            {
                serialized.Update();
                return serialized.FindProperty(path) ?? throw new InvalidOperationException($"'{key}' has no serialized property '{path}'.");
            }
            JToken Read()
            {
                var property = Property();
                return property.propertyType switch
                {
                    SerializedPropertyType.String => Token(property.stringValue),
                    SerializedPropertyType.Float => Token(property.floatValue),
                    SerializedPropertyType.Integer or SerializedPropertyType.Enum => Token(property.intValue),
                    SerializedPropertyType.Boolean => Token(property.boolValue),
                    SerializedPropertyType.ObjectReference => localReference ? Token(property.objectReferenceValue == (Object)desired)
                        : Reference(property.objectReferenceValue),
                    SerializedPropertyType.Color => Token(property.colorValue),
                    SerializedPropertyType.Vector2 => Token(property.vector2Value),
                    SerializedPropertyType.Vector3 => Token(property.vector3Value),
                    SerializedPropertyType.Quaternion => Token(property.quaternionValue),
                    _ => throw new InvalidOperationException($"'{key}' uses unsupported serialized type '{property.propertyType}'.")
                };
            }
            void Write()
            {
                var property = Property();
                switch (property.propertyType)
                {
                    case SerializedPropertyType.String: property.stringValue = (string)desired; break;
                    case SerializedPropertyType.Float: property.floatValue = (float)desired; break;
                    case SerializedPropertyType.Integer:
                    case SerializedPropertyType.Enum: property.intValue = (int)desired; break;
                    case SerializedPropertyType.Boolean: property.boolValue = (bool)desired; break;
                    case SerializedPropertyType.ObjectReference: property.objectReferenceValue = (Object)desired; break;
                    case SerializedPropertyType.Color: property.colorValue = (Color)desired; break;
                    case SerializedPropertyType.Vector2: property.vector2Value = (Vector2)desired; break;
                    case SerializedPropertyType.Vector3: property.vector3Value = (Vector3)desired; break;
                    case SerializedPropertyType.Quaternion: property.quaternionValue = (Quaternion)desired; break;
                    default: throw new InvalidOperationException($"'{key}' uses unsupported serialized type '{property.propertyType}'.");
                }
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
            fields.Add(key, (Read, localReference ? Token(true) : Token(desired), Write));
        }

        internal void SiblingOrder(string key, Transform value, int order) =>
            fields.Add(key, (() => Token(value.GetSiblingIndex()), Token(order), () => value.SetSiblingIndex(order)));

        internal void RectLayout(string key, RectTransform transform, Vector2 anchorMin, Vector2 anchorMax,
            Vector2 pivot, Rect rect, Vector2 parentSize = default)
        {
            JArray Layout(Vector2 min, Vector2 max, Vector2 p, Vector2 size, Vector3 position, Quaternion rotation, Vector3 scale) =>
                new(Token(min), Token(max), Token(p), Token(size), Token(position), Token(rotation), Token(scale));
            // 将参考尺寸下的左上角矩形换算成 Unity 的锚点空间。
            var anchor = anchorMin + Vector2.Scale(anchorMax - anchorMin, pivot);
            var anchorOffset = Vector2.Scale(parentSize, anchor - new Vector2(0, 1));
            var pivotOffset = Vector2.Scale(rect.size, pivot - new Vector2(0, 1));
            Vector3 position = new Vector2(rect.x, -rect.y) + pivotOffset - anchorOffset;
            var sizeDelta = rect.size - Vector2.Scale(parentSize, anchorMax - anchorMin);
            fields.Add(key, (
                () => Layout(transform.anchorMin, transform.anchorMax, transform.pivot, transform.sizeDelta,
                    transform.anchoredPosition3D, transform.localRotation, transform.localScale),
                Layout(anchorMin, anchorMax, pivot, sizeDelta, position, Quaternion.identity, Vector3.one),
                () =>
                {
                    transform.anchorMin = anchorMin;
                    transform.anchorMax = anchorMax;
                    transform.pivot = pivot;
                    transform.sizeDelta = sizeDelta;
                    transform.anchoredPosition3D = position;
                    transform.localRotation = Quaternion.identity;
                    transform.localScale = Vector3.one;
                    if (PrefabUtility.IsPartOfPrefabInstance(transform))
                        PrefabUtility.RecordPrefabInstancePropertyModifications(transform);
                }));
        }

        internal void Local(string key, GameObject instance, Component target, string sourcePath,
            string propertyPath, bool explicitValue, object value)
        {
            var source = PrefabUtility.GetCorrespondingObjectFromSourceAtPath(target, sourcePath);
            if (source == null)
                throw new InvalidOperationException($"'{key}' cannot locate its property source in '{sourcePath}'.");
            if (!overrides.TryGetValue(instance, out var changes))
                overrides.Add(instance, changes = new List<OverrideField>());
            var field = new OverrideField
            {
                Key = key, Source = source, Original = PrefabUtility.GetCorrespondingObjectFromOriginalSource(target), InstanceObject = target,
                Path = propertyPath, Explicit = explicitValue, Value = value
            };
            if (changes.Any(change => field.Path == change.Path && field.Source == change.Source))
                throw new InvalidOperationException($"'{key}' duplicates a managed instance property.");
            changes.Add(field);
        }

        internal JObject Snapshot()
        {
            var snapshot = new JObject(fields.Select(pair => new JProperty(pair.Key, pair.Value.read())));
            foreach (var pair in overrides)
            {
                var changes = Changes(pair.Key);
                foreach (var field in pair.Value)
                    snapshot.Add(field.Key, ReadOverride(field, changes));
            }
            return snapshot;
        }

        internal JObject Desired()
        {
            var result = new JObject(fields.Select(pair => new JProperty(pair.Key, pair.Value.desired.DeepClone())));
            foreach (var field in overrides.Values.SelectMany(items => items))
                result.Add(field.Key, field.Explicit ? ModificationValue(field.Modification()) : JValue.CreateNull());
            return result;
        }

        internal void Apply()
        {
            foreach (var field in fields.Where(pair => !pair.Key.EndsWith("/order", StringComparison.Ordinal)))
            {
                if (!JToken.DeepEquals(field.Value.read(), field.Value.desired))
                    field.Value.write();
            }
            // Reordering a sibling moves other siblings too. Apply lower positions first.
            foreach (var field in fields.Where(pair => pair.Key.EndsWith("/order", StringComparison.Ordinal))
                         .OrderBy(pair => (int)pair.Value.desired))
            {
                if (!JToken.DeepEquals(field.Value.read(), field.Value.desired))
                    field.Value.write();
            }
            foreach (var pair in overrides)
            {
                var changes = Changes(pair.Key).ToList();
                bool modified = false;
                foreach (var field in pair.Value)
                {
                    JToken desired = field.Explicit ? ModificationValue(field.Modification()) : JValue.CreateNull();
                    if (JToken.DeepEquals(ReadOverride(field, changes), desired))
                        continue;
                    changes.RemoveAll(change => field.Matches(change));
                    if (field.Explicit)
                        changes.Add(field.Modification());
                    modified = true;
                }
                // Retain unrelated and default overrides. An explicit equal value must still have its own entry.
                if (modified)
                    PrefabUtility.SetPropertyModifications(pair.Key, changes.ToArray());
            }
        }

        internal void RequireEffectiveOverrides()
        {
            foreach (var field in overrides.Values.SelectMany(values => values))
            {
                object Read(Object target)
                {
                    var serialized = new SerializedObject(target);
                    var property = serialized.FindProperty(field.Path);
                    // Unity stores Color overrides per channel even when SerializedObject exposes the full Color.
                    if (property == null && field.Path.StartsWith("m_fontColor.", StringComparison.Ordinal))
                    {
                        var color = serialized.FindProperty("m_fontColor").colorValue;
                        return field.Path[field.Path.Length - 1] switch { 'r' => color.r, 'g' => color.g, 'b' => color.b, _ => color.a };
                    }
                    if (property == null)
                        throw new InvalidOperationException($"'{field.Key}' has no effective serialized property '{field.Path}'.");
                    return property.propertyType switch
                    {
                        SerializedPropertyType.String => property.stringValue,
                        SerializedPropertyType.ObjectReference => property.objectReferenceValue,
                        SerializedPropertyType.Float => property.floatValue,
                        _ => throw new InvalidOperationException($"'{field.Key}' uses unsupported effective override type '{property.propertyType}'.")
                    };
                }
                var actual = Read(field.InstanceObject);
                var required = field.Explicit ? field.Value : Read(field.Source);
                if (actual is float current && required is float expected ? Mathf.Abs(current - expected) > 0.0001f :
                    !JToken.DeepEquals(Token(actual), Token(required)))
                    throw new InvalidOperationException($"'{field.Key}' did not resolve to its expected {(field.Explicit ? "local" : "inherited")} value.");
            }
        }

        static PropertyModification[] Changes(GameObject instance) =>
            PrefabUtility.GetPropertyModifications(instance) ?? Array.Empty<PropertyModification>();

        static JToken ReadOverride(OverrideField field, IEnumerable<PropertyModification> changes)
        {
            var matches = changes.Where(field.Matches).ToArray();
            if (matches.Length > 1)
                throw new InvalidOperationException($"'{field.Key}' has multiple local overrides.");
            return matches.Length == 0 ? JValue.CreateNull() : ModificationValue(matches[0]);
        }

        static JObject ModificationValue(PropertyModification change) => new()
        {
            ["value"] = change.objectReference != null ? JValue.CreateNull() : Token(change.value),
            ["reference"] = Reference(change.objectReference)
        };

        static JToken Reference(Object value)
        {
            if (value == null)
                return JValue.CreateNull();
            var (guid, fileId) = PrefabAssets.RequireIdentity(value);
            return new JArray(guid, fileId);
        }

        static JToken Token(object value) => value switch
        {
            null => JValue.CreateNull(),
            Object asset => Reference(asset),
            // JSON reloads floating-point values as Double. Keep the full precision of Unity's Single values.
            float number => new JValue((double)number),
            Vector2 v => new JArray((double)v.x, (double)v.y),
            Vector3 v => new JArray((double)v.x, (double)v.y, (double)v.z),
            Quaternion v => new JArray((double)v.x, (double)v.y, (double)v.z, (double)v.w),
            Color v => new JArray((double)v.r, (double)v.g, (double)v.b, (double)v.a),
            _ => new JValue(value)
        };

        internal static void RequireEqual(JObject actual, JObject expected, string path)
        {
            foreach (var key in actual.Properties().Select(property => property.Name)
                         .Union(expected.Properties().Select(property => property.Name)))
            {
                if (!JToken.DeepEquals(actual[key], expected[key]))
                    throw new InvalidOperationException($"'{path}' managed field '{key}' is {actual[key]?.ToString(Newtonsoft.Json.Formatting.None) ?? "missing"}; expected {expected[key]?.ToString(Newtonsoft.Json.Formatting.None) ?? "missing"}.");
            }
        }
    }
}
