using System.Collections.Generic;
using Newtonsoft.Json.Linq;

// The request fields are assigned by the JSON deserializer.
#pragma warning disable CS0649

namespace SmartUI.UGUI.Editor
{
    internal sealed class BuildRequest
    {
        public int SchemaVersion;
        public string RequestId, Operation, ProjectId, ResultPath;
        public Target[] Targets;
        public Dictionary<string, ImageAsset> Assets;
        public Dictionary<string, TextStyle> Fonts;
        public Dictionary<string, Control> Controls;
    }

    internal sealed class Target
    {
        public string Id, Kind, Path, BaseControlId, DirectSha256, DependencySha256, Background;
        public float Width, Height;
        public Node[] Nodes;
        public NodeOverride[] Overrides;
        public Dictionary<string, Slot> Slots;
        public ButtonSpec Button;
        public ExpectedTarget Expected;
    }

    internal sealed class Node
    {
        public string Id, ParentId, Kind, AssetId, Text, TextStyleId, Align, Overflow, ControlId;
        public bool? Wrap;
        public float X, Y, Width, Height;
        public float[] AnchorMin, AnchorMax, Pivot;
        public Dictionary<string, JToken> Parameters;
        public Dictionary<string, string> Bindings;
    }

    internal sealed class NodeOverride
    {
        public string NodeId, Property;
        public JToken Value;
    }

    internal sealed class ButtonSpec
    {
        public string TargetGraphic;
        public Dictionary<string, string> States;
    }

    internal sealed class Slot
    {
        public string NodeId, Component;
    }

    internal sealed class Parameter
    {
        public string SlotId, Property, ValueType;
        public bool Required;
    }

    internal sealed class SlotSource
    {
        public string ControlId;
        public long FileId;
    }

    internal sealed class Control
    {
        public string Path, Guid, Sha256, MetaSha256;
        public float Width, Height;
        public Dictionary<string, Slot> Slots;
        public Dictionary<string, SlotSource> SlotSources;
        public Dictionary<string, Parameter> Parameters;
    }

    internal sealed class ExpectedTarget
    {
        public string PrefabSha256, MetaSha256, BaselineSha256;
    }

    internal sealed class FileSnapshot
    {
        public string Sha256, MetaSha256;
    }

    internal sealed class ImageAsset
    {
        public string Path, OutputPath, Sha256;
        public int Width, Height;
        public JObject Geometry;
        public FileSnapshot Expected;
    }

    internal sealed class TextStyle
    {
        public string FontAssetPath, FontAssetGuid, MaterialPath, MaterialGuid;
        public string FontAssetSha256, FontAssetMetaSha256, MaterialSha256, MaterialMetaSha256;
        public float FontSize, LineHeight, LetterSpacing;
        public Paint Fill;
    }

    internal sealed class Paint
    {
        public string Color;
        public float Opacity;
    }

    internal sealed class BuildResult
    {
        public int SchemaVersion = 1;
        public string RequestId, RequestSha256, Status, Error;
        public TargetResult[] Targets;
    }

    internal sealed class TargetResult
    {
        public string Id, Kind, Status, Path, Guid, Sha256, MetaSha256, Error;
        public Dictionary<string, long> SlotBindings = new();
        public Dictionary<string, Dictionary<string, SlotFileId>> Instances = new();
        public string[] SourceGuids = System.Array.Empty<string>();
    }

    internal sealed class SlotFileId
    {
        public string ControlId, Guid;
        public long ScopeFileId, FileId;
    }

    internal sealed class TargetState
    {
        public string ProjectId, Path, Guid, DirectSha256, DependencySha256;
        public Target Declaration;
        public JObject Managed;
        public JObject Identities;
        public FileSnapshot Files;
    }

    internal sealed class SpriteState
    {
        public string ProjectId, Path, Guid;
        public FileSnapshot Files;
    }
}
