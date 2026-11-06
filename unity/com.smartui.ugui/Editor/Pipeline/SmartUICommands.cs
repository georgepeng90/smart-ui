using System.IO;
using Unity.Pipeline.Commands;

namespace SmartUI.UGUI.Editor
{
    internal static class SmartUICommands
    {
        [CliCommand("smartui", "execute", "Plan, apply, or verify a SmartUI Prefab build.", MainThreadRequired = true)]
        public static object Execute([CliArg("request_path", "Absolute path to the SmartUI request JSON.", Required = true)] string requestPath)
        {
            string status = PrefabBuild.Execute(requestPath);
            string id = Path.GetFileName(Path.GetDirectoryName(requestPath));
            return new { request_id = id, result_path = $"smart-ui/unity/operations/{id}/result.json", status };
        }
    }
}
