using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Serialization;
using FactoryForge.Parts;

namespace FactoryForge.Editor;

public class PartInstanceData
{
    [JsonPropertyName("id")] public string Id { get; set; } = string.Empty;
    [JsonPropertyName("type")] public string Type { get; set; } = string.Empty;
    [JsonPropertyName("position")] public float[] Position { get; set; } = new float[3];
    [JsonPropertyName("rotation")] public float[] Rotation { get; set; } = new float[3];

    /// <summary>
    /// Which work plane the part stands on (IP-15). Level 0 is the floor
    /// conveyors' plane; level n is <see cref="PartLayout.LevelHeight"/> x n
    /// above it.
    ///
    /// <see cref="Position"/>'s Y is measured within the part's own level: a
    /// belt on level 1 is written <c>"position": [x, 0.5, z], "level": 1</c>,
    /// the same 0.5 every belt on every level has. That keeps AGENTS.md's rule
    /// -- never bake a mounting height into a scene position -- true per level,
    /// and it is why a version-1 reader, which knows nothing of this key, must
    /// refuse a file that uses it rather than open it: it would put every
    /// raised part on the floor. Omitted when zero, so a scene with nothing
    /// raised is written exactly as it always was.
    ///
    /// In memory (undo snapshots, the clipboard, a placement) a part may carry
    /// its whole height in Y with level 0 instead; the two spellings build the
    /// same part, because <see cref="WorldY"/> is all a spawn reads.
    /// </summary>
    [JsonPropertyName("level")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)]
    public int Level { get; set; }

    /// <summary>The height the part's origin actually stands at.</summary>
    [JsonIgnore]
    public float WorldY => Position[1] + Level * PartLayout.LevelHeight;

    /// <summary>
    /// Restate the height the way a scene file stores it: the level it is
    /// nearest, and Y within that level. Every capture that ends up in a file
    /// goes through here, so a file never holds a raised part as a bare height.
    /// A level-0 part is left exactly as it was, off-plane Y and all.
    /// </summary>
    public PartInstanceData OnItsLevel()
    {
        float world = WorldY;
        Level = PartLayout.LevelOf(world);
        Position = new[] { Position[0], world - Level * PartLayout.LevelHeight, Position[2] };
        return this;
    }

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
    ///
    /// <b>Version 2 (IP-15)</b> added <see cref="PartInstanceData.Level"/>, with
    /// Y measured within the part's level. A file is stamped with the lowest
    /// version that can hold it (<see cref="VersionFor"/>): a scene with nothing
    /// raised is still written "1.0", byte for byte what a version-1 build
    /// wrote, and a version-1 build still opens it -- correctly, since it loses
    /// nothing. Only a scene that uses a level says "2.0", and a version-1
    /// build refuses it instead of putting every raised part on the floor.
    /// </summary>
    public const int CurrentMajorVersion = 2;

    [JsonPropertyName("name")] public string Name { get; set; } = "custom-scene";
    [JsonPropertyName("version")] public string Version { get; set; } = "1.0";
    [JsonPropertyName("parts")] public List<PartInstanceData> Parts { get; set; } = new();

    /// <summary>The lowest format version that holds these parts: "2.0" if any
    /// stands on a raised level, "1.0" otherwise. Stamping the lowest rather
    /// than the newest is what keeps every existing scene -- and every scene
    /// built without levels -- openable by the build before this one.</summary>
    public static string VersionFor(IEnumerable<PartInstanceData> parts)
    {
        foreach (var part in parts)
        {
            // Null-tolerant: the loader migrates before it has checked each
            // part, and a missing part is refused there, with its number.
            if (part is not null && part.Level != 0) return "2.0";
        }
        return "1.0";
    }

    public string ToJson()
    {
        var options = new JsonSerializerOptions { WriteIndented = true };
        return JsonSerializer.Serialize(this, options);
    }

    /// <summary>Parse without validating. Returns null rather than throwing on
    /// malformed JSON — <c>JsonSerializer.Deserialize</c> throws, which is how a
    /// corrupt file used to escape the loader as an exception rather than as a
    /// refusal. Prefer <see cref="TryParse(string, out SceneData?, out string)"/>,
    /// which also checks the structure.
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
    public static bool TryParse(string json, out SceneData? data, out string problem) =>
        TryParse(json, CurrentMajorVersion, out data, out problem);

    /// <summary>
    /// <see cref="TryParse(string, out SceneData?, out string)"/> as a build
    /// that reads format <paramref name="readerMajor"/> would.
    ///
    /// Exists for one test: that an older build refuses a newer file
    /// (<c>--self-test=scene</c>). The version check below is the one HP-07
    /// shipped in version-1 builds, so asking it with <c>readerMajor: 1</c> is
    /// asking what those builds do with a file this one writes.
    /// </summary>
    internal static bool TryParse(string json, int readerMajor, out SceneData? data,
                                  out string problem)
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

        if (major > readerMajor)
        {
            problem = $"it is a version {data.Version} scene file and this build reads " +
                      $"version {readerMajor}. Update FactoryForge to open it";
            data = null;
            return false;
        }

        data.Parts ??= new List<PartInstanceData>();

        // The migration hook, first used by version 2. It has nothing to move:
        // a version-1 file has no levels, and its Y is a level-0 Y, which is
        // exactly what version 2 means by a part with no "level".
        if (major < readerMajor) Migrate(data, major);

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

            // A level below the floor, or so high nothing frames it, is a typo
            // rather than a building -- refused here, before anything is
            // cleared, like every other shape of bad file (IP-15).
            if (part.Level < 0 || part.Level > PartLayout.MaxLevel)
            {
                problem = $"{where} ('{part.Type}') is on level {part.Level}; levels run " +
                          $"0 to {PartLayout.MaxLevel}";
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
        major = 1;
        if (string.IsNullOrWhiteSpace(version)) return true;   // written before the field existed

        string head = version.Split('.')[0].Trim();
        return int.TryParse(head, out major);
    }

    /// <summary>Bring an older scene up to the current format. Version 1 to 2
    /// moves nothing (see the call); what changes is the stamp, which a save
    /// re-derives with <see cref="VersionFor"/> anyway.</summary>
    private static void Migrate(SceneData data, int fromMajor)
    {
        data.Version = VersionFor(data.Parts);
    }
}
