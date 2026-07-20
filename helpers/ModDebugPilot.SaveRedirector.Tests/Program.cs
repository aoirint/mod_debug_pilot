using ModDebugPilot.SaveRedirector;

string root = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "mdp-save-root"));
// LCBetterSaves 1.7.3 enumerates, renames, and deletes these ES3 path families.
// Resolving all of them through the same policy is the compatibility contract:
// the mod can manage its extra slots without ever seeing the user's normal saves.
string[] compatiblePaths =
[
    "LCSaveFile1",
    "LCSaveFile16",
    "LGU_1.json",
    "TempFile1",
    "LGUTempFile1",
    Path.Combine("nested", "LCSaveFile1"),
];
foreach (string relativePath in compatiblePaths)
{
    string expected = Path.Combine(root, relativePath);
    string actual = SavePathPolicy.Resolve(root, relativePath);
    if (!string.Equals(expected, actual, StringComparison.OrdinalIgnoreCase))
    {
        return 1;
    }
}

string[] rejected = ["", "..\\normal-save", Path.GetFullPath("normal-save")];
foreach (string value in rejected)
{
    try
    {
        SavePathPolicy.Resolve(root, value);
        return 2;
    }
    catch (ArgumentException)
    {
        // Expected: traversal and absolute paths must fail closed.
    }
}

return 0;
