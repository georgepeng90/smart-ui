using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;
using UnityEditor;

namespace SmartUI.UGUI.Editor
{
    internal static class BuildFiles
    {
        static readonly JsonSerializerSettings JsonSettings = new()
        {
            ContractResolver = new DefaultContractResolver { NamingStrategy = new SnakeCaseNamingStrategy() },
            DateParseHandling = DateParseHandling.None,
            Formatting = Formatting.Indented,
            TypeNameHandling = TypeNameHandling.None
        };

        internal static string RelativePath(string path)
        {
            if (string.IsNullOrEmpty(path) || path.Contains('\\') || path.Contains(':') ||
                path.Split('/').Any(segment => segment.Length == 0 || segment is "." or ".." ||
                    segment.EndsWith(".", StringComparison.Ordinal) || segment.Trim() != segment ||
                    segment.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0))
                throw new InvalidOperationException($"Invalid project-relative path '{path}'.");
            string current = PrefabAssets.ProjectPath;
            foreach (string segment in path.Split('/'))
            {
                current = Path.Combine(current, segment);
                if ((File.Exists(current) || Directory.Exists(current)) &&
                    (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException($"'{path}' contains a linked path at '{current}'.");
            }
            return current;
        }

        internal static void RequireId(string value, string kind)
        {
            if (value == null || !Regex.IsMatch(value, @"\A[A-Za-z0-9][A-Za-z0-9_.-]*\z"))
                throw new InvalidOperationException($"Invalid {kind} ID '{value}'.");
        }

        internal static string StatePath(string kind, string id)
        {
            RequireId(id, kind);
            return RelativePath($"smart-ui/unity/import-state/{kind}/{id}.json");
        }

        internal static FileSnapshot Snapshot(string assetPath) => new()
        {
            Sha256 = PrefabAssets.FileHash(RelativePath(assetPath)),
            MetaSha256 = PrefabAssets.FileHash(RelativePath(assetPath + ".meta"))
        };

        internal static void RequireSnapshot(string path, FileSnapshot actual, FileSnapshot expected)
        {
            if (expected == null || actual.Sha256 != expected.Sha256 || actual.MetaSha256 != expected.MetaSha256)
                throw new InvalidOperationException($"'{path}' or its .meta changed since the recorded snapshot.");
        }

        internal static void RequireWritable(string path)
        {
            foreach (var candidate in new[] { path, path + ".meta" })
            {
                var physical = RelativePath(candidate);
                if (File.Exists(physical) && (!AssetDatabase.IsOpenForEdit(candidate) ||
                    (File.GetAttributes(physical) & FileAttributes.ReadOnly) != 0))
                    throw new InvalidOperationException($"'{candidate}' is read-only or locked for editing.");
            }
        }

        internal static T Deserialize<T>(string json)
        {
            using var text = new StringReader(json);
            using var reader = new JsonTextReader(text) { DateParseHandling = DateParseHandling.None };
            var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (reader.Read())
                throw new InvalidDataException("Unexpected data after the JSON object.");
            return value.ToObject<T>(JsonSerializer.Create(JsonSettings));
        }

        internal static T Read<T>(string path) where T : class => File.Exists(path) ? Deserialize<T>(File.ReadAllText(path)) : null;

        internal static string Serialize(object value) => JsonConvert.SerializeObject(value, JsonSettings) + "\n";

        internal static void Write(string path, object value, bool replace = false)
        {
            string json = Serialize(value);
            if (replace && File.Exists(path) && File.ReadAllText(path) == json)
                return;
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    var bytes = new UTF8Encoding(false).GetBytes(json);
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                if (replace && File.Exists(path))
                    File.Replace(temporary, path, null);
                else
                    File.Move(temporary, path);
            }
            finally
            {
                if (File.Exists(temporary))
                    File.Delete(temporary);
            }
        }

        static string PendingPath(string statePath) => statePath + ".pending";

        internal static void RequireNoPending(string statePath)
        {
            var pending = Read<JObject>(PendingPath(statePath));
            if (pending != null)
            {
                var path = (string)pending["path"];
                var current = Snapshot(path);
                throw new InvalidOperationException($"Unfinished write to '{path}'. Current asset SHA256 is '{current.Sha256}', .meta SHA256 is '{current.MetaSha256}'. Review '{PendingPath(statePath)}' before retrying.");
            }
        }

        internal static void MarkPending(string statePath, string assetPath) =>
            Write(PendingPath(statePath), new { path = assetPath, before = Snapshot(assetPath) });

        internal static void Complete(string statePath, object state)
        {
            Write(statePath, state, true);
            File.Delete(PendingPath(statePath));
        }
    }
}
