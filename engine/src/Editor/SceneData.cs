using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace FactoryForge.Editor;

public class PartInstanceData
{
    [JsonPropertyName("id")] public string Id { get; set; } = string.Empty;
    [JsonPropertyName("type")] public string Type { get; set; } = string.Empty;
    [JsonPropertyName("position")] public float[] Position { get; set; } = new float[3];
    [JsonPropertyName("rotation")] public float[] Rotation { get; set; } = new float[3];

    /// <summary>
    /// Everything the part was tuned to. Without it a scene file described only
    /// where the machines stood, not how they were set up, so reloading reset
    /// every value to its default. Unknown keys are ignored on load, so a scene
    /// from a newer build still opens.
    /// </summary>
    [JsonPropertyName("properties")] public Dictionary<string, string>? Properties { get; set; }
}

public class SceneData
{
    /// <summary>
    /// The scene-file format this build writes, and the newest it can read.
    ///
    /// It was written into every scene file from the beginning and read
    /// absolutely nowhere — <c>grep -rn "\.Version" engine/src/</c> found not one
    /// reader (HP-07). Unknown *keys* are ignored on load, which is right for a
    /// forward-compatible field and wrong for a whole future format: a version 2
    /// scene would have loaded quietly, dropped everything it did not recognise
    /// and then offered to save the remains back over the original. That matters
    /// from the moment students start sharing scene files with each other.
    /// </summary>
    public const int CurrentMajorVersion = 1;

    [JsonPropertyName("name")] public string Name { get; set; } = "custom-scene";
    [JsonPropertyName("version")] public string Version { get; set; } = "1.0";
    [JsonPropertyName("parts")] public List<PartInstanceData> Parts { get; set; } = new();

    public string ToJson()
    {
        var options = new JsonSerializerOptions { WriteIndented = true };
        return JsonSerializer.Serialize(this, options);
    }

    /// <summary>Parse without validating. Returns null rather than throwing on
    /// malformed JSON — <c>JsonSerializer.Deserialize</c> throws, which is how a
    /// corrupt file used to escape the loader as an exception rather than as a
    /// refusal. Prefer <see cref="TryParse"/>, which also checks the structure.
    /// </summary>
    public static SceneData? FromJson(string json)
    {
        try
        {
            return JsonSerializer.Deserialize<SceneData>(json);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    /// <summary>
    /// The one boundary a scene file has to cross before anything destructive
    /// happens (HP-02, HP-07, HP-15).
    ///
    /// The loader used to clear the open scene and *then* start parsing, so
    /// every one of these failures cost the user the scene they already had.
    /// Three distinct shapes reached it, and "parse first" only covers the
    /// first: <c>Deserialize</c> throwing on malformed JSON, it returning null
    /// on a file containing <c>null</c>, and a part whose position array is too
    /// short to build a Vector3 from — which threw later still, half-way through
    /// rebuilding the scene, with some parts already placed.
    /// </summary>
    /// <param name="problem">Why it was refused, phrased for somebody who just
    /// picked a file, not for a stack trace.</param>
    public static bool TryParse(string json, out SceneData? data, out string problem)
    {
        data = null;
        problem = "";

        if (string.IsNullOrWhiteSpace(json))
        {
            problem = "the file is empty";
            return false;
        }

        try
        {
            data = JsonSerializer.Deserialize<SceneData>(json);
        }
        catch (JsonException ex)
        {
            problem = $"it is not valid JSON ({ex.Message})";
            return false;
        }

        if (data is null)
        {
            problem = "it contains no scene";
            return false;
        }

        if (!TryReadMajorVersion(data.Version, out int major))
        {
            problem = $"its format version '{data.Version}' is not a version number";
            data = null;
            return false;
        }

        if (major > CurrentMajorVersion)
        {
            problem = $"it is a version {data.Version} scene file and this build reads " +
                      $"version {CurrentMajorVersion}. Update FactoryForge to open it";
            data = null;
            return false;
        }

        // The migration hook. Empty on purpose: version 1 is the only format
        // there has ever been, and the point of naming the seam now is that the
        // first format change has somewhere to go other than "half-load it".
        if (major < CurrentMajorVersion) Migrate(data, major);

        data.Parts ??= new List<PartInstanceData>();

        var seen = new HashSet<string>();
        for (int i = 0; i < data.Parts.Count; i++)
        {
            var part = data.Parts[i];
            string where = $"part {i + 1} of {data.Parts.Count}";

            if (part is null)
            {
                problem = $"{where} is missing";
                data = null;
                return false;
            }

            if (string.IsNullOrWhiteSpace(part.Type))
            {
                problem = $"{where} does not say what type of part it is";
                data = null;
                return false;
            }

            if (part.Position is not { Length: >= 3 })
            {
                problem = $"{where} ('{part.Type}') has no usable position";
                data = null;
                return false;
            }

            if (part.Rotation is not { Length: >= 3 })
            {
                problem = $"{where} ('{part.Type}') has no usable rotation";
                data = null;
                return false;
            }

            // Two parts under one instance id means two machines answering one
            // PLC output, with nothing on screen to say which (HP-15). Adopting
            // them is worse than refusing the file: the second part registers no
            // tags of its own and the pair drive each other's.
            if (part.Id is { Length: > 0 } && !seen.Add(part.Id))
            {
                problem = $"two parts are both called '{part.Id}'; an instance id is a tag " +
                          "prefix and has to be unique";
                data = null;
                return false;
            }
        }

        return true;
    }

    /// <summary>Turn a "1.0" or "2" into its major number.</summary>
    private static bool TryReadMajorVersion(string? version, out int major)
    {
        major = CurrentMajorVersion;
        if (string.IsNullOrWhiteSpace(version)) return true;   // written before the field existed

        string head = version.Split('.')[0].Trim();
        return int.TryParse(head, out major);
    }

    /// <summary>Bring an older scene up to the current format. Nothing to do
    /// yet; this exists so the first format change has a place to land that is
    /// not "load it anyway and hope".</summary>
    private static void Migrate(SceneData data, int fromMajor)
    {
        data.Version = $"{CurrentMajorVersion}.0";
    }
}
