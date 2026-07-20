using ModDebugPilot.SaveRedirector;

string root = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "mdp-save-root"));
string expected = Path.Combine(root, "nested", "LCSaveFile1");
string actual = SavePathPolicy.Resolve(root, Path.Combine("nested", "LCSaveFile1"));
if (!string.Equals(expected, actual, StringComparison.OrdinalIgnoreCase))
{
    return 1;
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
