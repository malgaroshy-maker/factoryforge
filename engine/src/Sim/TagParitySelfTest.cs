using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json.Nodes;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Runs engine/fixtures/tag_cases.json against the C# tag model.
///
/// <code>godot --headless --path engine -- --self-test=parity</code>
///
/// The Python sidecar mirrors this model by hand (see the comment on
/// <see cref="Tag"/>), and nothing enforced that the two stayed in step until
/// now. tests/test_tag_parity.py runs the identical fixture against the Python
/// side; a coercion or epsilon rule changed on one side and not the other fails
/// here or there instead of shipping quietly. See FF-29.
/// </summary>
public partial class TagParitySelfTest : Node
{
    private readonly List<string> _failures = new();

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    public override void _Ready()
    {
        try
        {
            var root = JsonNode.Parse(FixtureFile.Read("res://fixtures/tag_cases.json"))!.AsObject();

            CheckCoerce(root["coerce"]!.AsArray());
            CheckDiffers(root["differs"]!.AsArray());
            CheckStore(root["store"]!.AsArray());
        }
        catch (Exception ex)
        {
            _failures.Add(ex.Message);
            GD.PrintErr($"  FAIL  threw: {ex.GetType().Name}: {ex.Message}");
        }

        if (_failures.Count == 0)
        {
            GD.Print("self-test parity: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test parity: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    private void CheckCoerce(JsonArray cases)
    {
        foreach (var node in cases)
        {
            var c = node!.AsObject();
            var type = ParseType(c["type"]!.GetValue<string>());
            bool expectError = c["error"]?.GetValue<bool>() ?? false;
            object input = FromCase(c, "input");

            // A throwaway tag of the target type; Coerce depends only on Type,
            // not on the tag's current value.
            var probe = new Tag("case", "case", type, TagKind.Output);
            try
            {
                object result = probe.Coerce(input);
                Expect(!expectError, $"{c["type"]}: {input} should be rejected, got {result}");
                if (!expectError)
                {
                    object expected = FromJson(c["expect"]!);
                    Expect(ValuesMatch(result, expected),
                        $"{c["type"]}: {input} -> expected {expected}, got {result}");
                }
            }
            catch (ArgumentException)
            {
                Expect(expectError, $"{c["type"]}: {input} should be accepted, was rejected");
            }
        }
    }

    private void CheckDiffers(JsonArray cases)
    {
        foreach (var node in cases)
        {
            var c = node!.AsObject();
            var type = ParseType(c["type"]!.GetValue<string>());
            object current = FromCase(c, "current");
            object candidate = FromCase(c, "candidate");
            bool expectError = c["error"]?.GetValue<bool>() ?? false;

            var tag = new Tag("case", "case", type, TagKind.Output, current);
            try
            {
                bool differs = tag.Differs(candidate);
                Expect(!expectError,
                    $"{c["type"]}: differs({current}, {candidate}) should be rejected, got {differs}");
                if (expectError) continue;
                bool expected = c["expect"]!.GetValue<bool>();
                Expect(differs == expected,
                    $"{c["type"]}: differs({current}, {candidate}) expected {expected}, got {differs}");
            }
            catch (ArgumentException)
            {
                Expect(expectError,
                    $"{c["type"]}: differs({current}, {candidate}) should be answered, was rejected");
            }
        }
    }

    /// <summary>HP-18.3: the epsilon has to suppress the <em>store</em>, not
    /// only the comparison. Both tables reported "no change" for a sub-epsilon
    /// move and then stored the new value anyway, so the reference crept and a
    /// signal drifting 1e-9 a scan published on every scan after all.</summary>
    private void CheckStore(JsonArray cases)
    {
        foreach (var node in cases)
        {
            var c = node!.AsObject();
            var type = ParseType(c["type"]!.GetValue<string>());
            var probe = new Tag("case", "case", type, TagKind.Output, FromCase(c, "current"));
            var table = new TagTable();
            table.Add(probe);

            bool changed = table.Set("case", FromCase(c, "write"));
            bool expectedChanged = c["expect_changed"]!.GetValue<bool>();
            Expect(changed == expectedChanged,
                $"{c["type"]}: set({c["current"]} <- {c["write"]}) reported changed={changed}, " +
                $"expected {expectedChanged}");

            // Through Coerce, so the fixture's 1.0 and the tag's 1.0 are the
            // same kind of thing whichever way the JSON reader guessed.
            object expectedStored = probe.Coerce(FromCase(c, "expect_stored"));
            Expect(ValuesMatch(table.Visible("case"), expectedStored),
                $"{c["type"]}: set({c["current"]} <- {c["write"]}) stored " +
                $"{table.Visible("case")}, expected {expectedStored}");
        }
    }

    private static bool ValuesMatch(object result, object expected)
    {
        if (result is double rd && expected is double ed) return Math.Abs(rd - ed) < 1e-9;
        return Equals(result, expected);
    }

    private static TagType ParseType(string t) => t switch
    {
        "bit" => TagType.Bit,
        "int" => TagType.Int,
        "float" => TagType.Float,
        _ => throw new ArgumentException($"unknown fixture type {t}"),
    };

    /// <summary>One value out of a case, honouring the `_special` escape.
    ///
    /// JSON cannot spell a non-finite float, so a case that needs one names it
    /// -- `"input_special": "nan"` -- and both readers of the fixture decode
    /// the same three names. tests/test_tag_parity.py does the identical
    /// thing.</summary>
    private static object FromCase(JsonObject c, string key)
    {
        if (c[$"{key}_special"] is { } special)
            return special.GetValue<string>() switch
            {
                "nan" => double.NaN,
                "inf" => double.PositiveInfinity,
                "-inf" => double.NegativeInfinity,
                var other => throw new ArgumentException($"unknown special {other}"),
            };
        return FromJson(c[key]!);
    }

    /// <summary>Mirrors TagBusServer.ToClr -- the same decode a real `write` or
    /// `force` message goes through, so this exercises the real path rather
    /// than a parallel one.</summary>
    private static object FromJson(JsonNode node)
    {
        var v = node.AsValue();
        if (v.TryGetValue<bool>(out var b)) return b;
        if (v.TryGetValue<int>(out var i)) return i;
        if (v.TryGetValue<double>(out var d)) return d;
        return v.ToString();
    }
}
