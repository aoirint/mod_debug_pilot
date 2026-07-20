using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;

using BepInEx;
using HarmonyLib;
using UnityEngine;

namespace ModDebugPilot.SaveRedirector;

/// <summary>Redirects Easy Save 3 files into an Agent-selected instance directory.</summary>
[BepInPlugin(MyPluginInfo.PLUGIN_GUID, MyPluginInfo.PLUGIN_NAME, MyPluginInfo.PLUGIN_VERSION)]
[BepInProcess("Lethal Company.exe")]
public sealed class Plugin : BaseUnityPlugin
{
    internal const string SaveRootEnvironmentVariable = "MODDEBUGPILOT_SAVE_ROOT";

    internal static string SaveRoot { get; private set; } = string.Empty;

    private void Awake()
    {
        try
        {
            SaveRoot = SavePathPolicy.NormalizeRoot(
                Environment.GetEnvironmentVariable(SaveRootEnvironmentVariable)
            );
            Directory.CreateDirectory(SaveRoot);
            new Harmony(MyPluginInfo.PLUGIN_GUID).PatchAll(Assembly.GetExecutingAssembly());

            string rootHash = Convert.ToBase64String(
                SHA256.Create().ComputeHash(Encoding.UTF8.GetBytes(SaveRoot))
            );
            Logger.LogInfo(
                $"[MODDEBUGPILOT] save_redirect_ready version={MyPluginInfo.PLUGIN_VERSION} root_sha256={rootHash}"
            );
        }
        catch (Exception exception)
        {
            Logger.LogFatal(
                $"[MODDEBUGPILOT] save_redirect_failed error={exception.GetType().Name}"
            );
            Application.Quit(86);
        }
    }
}

[HarmonyPatch(typeof(ES3Settings), nameof(ES3Settings.FullPath), MethodType.Getter)]
internal static class ES3SettingsFullPathPatch
{
    private static bool Prefix(ES3Settings __instance, ref string __result)
    {
        if (
            __instance.location != ES3.Location.File
            || __instance.directory != ES3.Directory.PersistentDataPath
        )
        {
            return true;
        }

        try
        {
            __result = SavePathPolicy.Resolve(Plugin.SaveRoot, __instance.path);
            Directory.CreateDirectory(Path.GetDirectoryName(__result)!);
            return false;
        }
        catch (Exception)
        {
            __result = Path.Combine(Plugin.SaveRoot, "blocked-path.es3");
            return false;
        }
    }
}

/// <summary>Framework-free path confinement used by the Harmony boundary.</summary>
public static class SavePathPolicy
{
    /// <summary>Validate and canonicalize an Agent-provided save root.</summary>
    public static string NormalizeRoot(string? value)
    {
        if (string.IsNullOrWhiteSpace(value) || !Path.IsPathRooted(value))
        {
            throw new ArgumentException("An absolute save root is required.", nameof(value));
        }

        return Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    /// <summary>Resolve one Easy Save path and prove that it remains below the root.</summary>
    public static string Resolve(string root, string? requestedPath)
    {
        string normalizedRoot = NormalizeRoot(root);
        if (string.IsNullOrWhiteSpace(requestedPath) || Path.IsPathRooted(requestedPath))
        {
            throw new ArgumentException("A relative save path is required.", nameof(requestedPath));
        }

        string candidate = Path.GetFullPath(Path.Combine(normalizedRoot, requestedPath));
        string rootPrefix = normalizedRoot + Path.DirectorySeparatorChar;
        if (!candidate.StartsWith(rootPrefix, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("Save path escaped its instance root.", nameof(requestedPath));
        }

        return candidate;
    }
}
