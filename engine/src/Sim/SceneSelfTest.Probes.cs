using System.Collections.Generic;
using System.Linq;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Every catalog part, driven, has to do something (IP-07).
///
/// This replaces a check that set every output in the scene high, ticked the
/// dispatch twice and then asserted <c>Expect(true, …)</c>. It could not fail.
/// The editor's dispatch catches a part that throws and carries on, so even the
/// one thing it claimed to catch never reached it, and a part with its tick
/// deleted passed exactly as a working one did.
///
/// Now each part states its own probe (<see cref="IPart.Probe"/>): a stimulus
/// and the effect the part should report. The test lays one of every part out
/// on a fresh scene, far enough apart that none reaches another, and runs them
/// all together over real physics ticks — real ticks, not direct calls into the
/// dispatch, because several of these parts sense through the physics server
/// (raycasts, areas), which only moves on a physics step.
///
/// For each part, in order:
/// <list type="number">
/// <item><b>Rest.</b> Nothing driven for <see cref="RestTicks"/>. The effect
/// must not appear. A predicate that already holds here would pass a part that
/// ignores every tag it has, so this is what makes the next step mean
/// anything.</item>
/// <item><b>Stimulus.</b> The probe's outputs are written, its carton parked,
/// its controls operated, its companion driven.</item>
/// <item><b>Effect.</b> Within the probe's own tick budget the effect must
/// appear. A part that does not do it fails here, by name, with the stimulus
/// and the effect in the message.</item>
/// </list>
///
/// And for the catalog as a whole: every part must have a probe or be on
/// <see cref="ProbeExemptions"/> with a reason, so a new part cannot skip this
/// by saying nothing.
/// </summary>
public partial class SceneSelfTest
{
    /// <summary>
    /// Catalog parts with no probe, and why. Every entry is a claim that the
    /// part has nothing a tick could be seen to do, and the claim is checked
    /// rather than trusted: an exempt type must still be in the catalog, must
    /// still have no probe, and must still declare no tags at all. A part
    /// that grows I/O loses its exemption the same run.
    /// </summary>
    private static readonly Dictionary<string, string> ProbeExemptions = new()
    {
        ["Chute"] = "declares no tags and has no tick: a static ramp, with nothing a controller can "
                    + "drive and nothing it reports. A carton sliding down it is Jolt's work, not the "
                    + "part's, and the chute's settings are covered by this test's save/load round trip.",
    };

    private const string ProbeScenePath = "user://selftest_probe_scene.json";

    /// <summary>Metres between probed parts: twice the widest default reach
    /// found among them (the area scanner's 2 m warning field; a pump fills
    /// tanks within 1.6 m, a fan cools within 1.2 m), so no part is inside
    /// another's.</summary>
    private const float ProbeSpacing = 4.0f;

    /// <summary>Ticks for the scene's parts to build their geometry before
    /// anything is asked of them.</summary>
    private const int ReadyTicks = 5;

    /// <summary>Ticks of nothing driven, during which no effect may
    /// appear.</summary>
    private const int RestTicks = 30;

    private sealed class ProbeRun
    {
        public required string Type;
        public required string Id;
        public required Node3D Node;
        public required PartProbe Probe;
        public string? CompanionId;
        public ProbeReading? Reading;
        public int HeldAfter = -1;
        public bool HeldAtRest;
        public string? Misread;
    }

    private readonly List<ProbeRun> _probes = new();
    private int _probeTick;
    private int _probeBudget;
    private int _probesPlaced;

    /// <summary>
    /// Read every catalog part's probe off a bare instance, check the catalog
    /// is covered, then write and load a scene with each probed part (and any
    /// companion it asked for) on a grid of its own.
    /// </summary>
    private void StartProbes()
    {
        var data = new SceneData { Name = "selftest-probes" };
        int slot = 0;

        foreach (string type in ProbeExemptions.Keys)
        {
            Expect(AllTypes.Contains(type),
                   $"probe exemption for '{type}' names a part the catalog no longer has");
        }

        foreach (string type in AllTypes)
        {
            var bare = PartCatalog.Create(type);
            PartProbe? probe = (bare as IPart)?.Probe;
            bare?.Free();

            bool exempt = ProbeExemptions.TryGetValue(type, out string? why);
            if (probe is null)
            {
                Expect(exempt,
                       $"{type} has no Probe and is not on the probe exemption list. Give it an "
                       + "IPart.Probe that drives it and names what it reports (docs/PART_AUTHORING.md, "
                       + "Step 3), or add it to SceneSelfTest.ProbeExemptions with the reason nothing "
                       + "a tick does can be seen.");
                if (!exempt) continue;

                // The only reason accepted today is "there is nothing to
                // probe". Every part with a tag has a stimulus available --
                // outputs to drive, a carton to sense, a hand, a companion --
                // so an exemption for one is a probe nobody wrote yet.
                int tags = PartCatalog.TagSuffixes(type).Count;
                Expect(tags == 0,
                       $"{type} is exempt from probing but declares {tags} tag(s); a part with I/O can be "
                       + "probed, so write it one instead");
                GD.Print($"  exempt  {type}: {why}");
                continue;
            }
            Expect(!exempt,
                   $"{type} is on the probe exemption list but has a Probe; remove the stale exemption");

            float x = -14.0f + slot % 8 * ProbeSpacing;
            float z = 4.0f + slot / 8 * ProbeSpacing;
            slot++;
            _probesPlaced++;

            string id = $"probe_{type.ToLowerInvariant()}";
            data.Parts.Add(new PartInstanceData
            {
                Id = id, Type = type,
                Position = new[] { x, PartLayout.WorkPlaneY, z },
                Rotation = new[] { 0.0f, 0.0f, 0.0f },
            });

            if (probe.Companion is { } companion)
            {
                Expect(PartCatalog.IsKnownType(companion.Type),
                       $"{type}'s probe asks for a companion '{companion.Type}' the catalog does not have");
                data.Parts.Add(new PartInstanceData
                {
                    Id = $"{id}_companion", Type = companion.Type,
                    // Unrotated, so the part's frame is the world's and the
                    // offset can be added as it stands.
                    Position = new[] { x + companion.Offset.X, PartLayout.WorkPlaneY + companion.Offset.Y,
                                       z + companion.Offset.Z },
                    Rotation = new[] { 0.0f, 0.0f, 0.0f },
                });
            }
        }

        using (var file = Godot.FileAccess.Open(ProbeScenePath, Godot.FileAccess.ModeFlags.Write))
        {
            file?.StoreString(data.ToJson());
        }
        Expect(Editor!.LoadSceneFromFile(ProbeScenePath), "the probe scene loads");
        _probeTick = 0;
    }

    /// <summary>One physics tick of the probe phase. True when it has
    /// finished, whether or not everything passed.</summary>
    private bool StepProbes()
    {
        _probeTick++;

        if (_probeTick == ReadyTicks) { BindProbes(); return _probes.Count == 0; }
        if (_probeTick < ReadyTicks) return false;

        int restEnd = ReadyTicks + RestTicks;
        if (_probeTick <= restEnd)
        {
            foreach (var run in _probes)
            {
                if (run.HeldAfter != int.MaxValue) Evaluate(run, resting: true);
            }
            if (_probeTick == restEnd) ApplyStimuli();
            return false;
        }

        int driven = _probeTick - restEnd;
        bool pending = false;
        foreach (var run in _probes)
        {
            if (run.HeldAfter >= 0 || driven > run.Probe.WithinTicks) continue;
            if (Evaluate(run, resting: false)) run.HeldAfter = driven;
            else pending = true;
        }
        if (pending && driven <= _probeBudget) return false;

        ReportProbes();
        return true;
    }

    /// <summary>Find the placed parts and read their probes again, off the
    /// instances that will actually be ticked — the predicate closes over
    /// the part it came from.</summary>
    private void BindProbes()
    {
        var placed = Editor!.PlacedPartIds().ToHashSet();
        foreach (string type in AllTypes)
        {
            string id = $"probe_{type.ToLowerInvariant()}";
            if (!placed.Contains(id)) continue;

            var node = Editor.NodeFor(id);
            if (node is not IPart { Probe: { } probe })
            {
                Expect(false, $"{type}: the placed part has no probe, though a bare one did");
                continue;
            }

            var run = new ProbeRun { Type = type, Id = id, Node = node!, Probe = probe };
            if (probe.Companion is not null)
            {
                run.CompanionId = $"{id}_companion";
                Expect(placed.Contains(run.CompanionId), $"{type}'s companion was placed");
            }

            // A probe with no stimulus asserts only that something happens
            // eventually, on its own -- which a clock would satisfy.
            Expect(probe.Drive.Count > 0 || probe.Carton is not null || probe.Operate.Count > 0
                   || probe.Companion is not null,
                   $"{type}'s probe has no stimulus: nothing driven, parked, operated or placed beside it");

            // The rule the whole probe rests on: drive what a controller
            // drives, and nothing it reads. A probe that wrote its own feedback
            // would pass on a part that did nothing.
            ExpectOutputs(type, id, probe.Drive, "drives");
            if (probe.Companion is { } companion && run.CompanionId is { } cid)
                ExpectOutputs($"{type}'s companion {companion.Type}", cid, companion.Drive, "drives");

            run.Reading = new ProbeReading(Tags, id, Snapshot(id));
            _probes.Add(run);
            _probeBudget = System.Math.Max(_probeBudget, probe.WithinTicks);
        }
        // A part missing its probe is reported by StartProbes; this is the
        // other way round -- a probe written into the scene that never came
        // back as a part to probe. And there has to be something to probe at
        // all, or every loop above passes by never running (gotcha 16).
        Expect(_probes.Count == _probesPlaced && _probesPlaced > 0,
               $"every part written into the probe scene came back to be probed ({_probes.Count} of {_probesPlaced})");
    }

    private void ExpectOutputs(string who, string id, IReadOnlyDictionary<string, object> drive, string verb)
    {
        foreach (string suffix in drive.Keys)
        {
            var tag = Tags.Get($"{id}.{suffix}");
            Expect(tag is { Kind: TagKind.Output },
                   $"{who}'s probe {verb} '{suffix}', which is "
                   + (tag is null ? "not one of its tags" : "an input: a probe may only drive outputs"));
        }
    }

    /// <summary>Everything the stimulus consists of, applied in one tick.
    /// Each part's baseline is retaken here, so "rises" means rises from
    /// where rest left it.</summary>
    private void ApplyStimuli()
    {
        foreach (var run in _probes)
        {
            run.Reading = new ProbeReading(Tags, run.Id, Snapshot(run.Id));

            Write(run.Id, run.Probe.Drive, run.Type);
            if (run.Probe.Companion is { } companion && run.CompanionId is { } cid)
                Write(cid, companion.Drive, $"{run.Type}'s companion");

            if (run.Probe.Carton is { } carton) ParkCarton(run.Node, carton);

            foreach (string region in run.Probe.Operate)
            {
                ((IPart)run.Node).Operate(new PartOperate(Tags, IdsUnder(run.Id), run.Id, region,
                                                          tagId => Tags.Set(tagId, true)));
            }
        }
    }

    private void Write(string id, IReadOnlyDictionary<string, object> drive, string who)
    {
        foreach (var (suffix, value) in drive)
        {
            // Not an output, or not a tag at all: ExpectOutputs has already
            // failed the probe for it, and writing it anyway would let the
            // probe's own write be what satisfies its predicate.
            string tagId = $"{id}.{suffix}";
            if (Tags.Get(tagId) is not { Kind: TagKind.Output }) continue;
            try
            {
                Tags.Set(tagId, value);
            }
            catch (System.Exception ex)
            {
                Expect(false, $"{who}'s probe could not write {value} to '{suffix}': {ex.Message}");
            }
        }
    }

    /// <summary>A carton parked frozen and kinematic where the probe asked,
    /// the way <c>--self-test=newparts</c> parks one under the scanner: it has
    /// to stay put across the ticks the physics server takes to notice it,
    /// rather than falling out of the beam first. (A static freeze was tried
    /// as well and every carton probe passed with it; kinematic is kept to
    /// match newparts, not because static fails.)</summary>
    private void ParkCarton(Node3D part, ProbeCarton carton)
    {
        var box = new BoxPhysics { Name = $"ProbeCarton_{part.Name}", IsTall = carton.Tall, IsMetal = carton.Metal };
        Editor!.GetParent().AddChild(box);
        box.GlobalPosition = part.GlobalTransform * (carton.At + new Vector3(0, box.Height / 2.0f, 0));
        box.FreezeMode = RigidBody3D.FreezeModeEnum.Kinematic;
        box.Freeze = true;
    }

    private bool Evaluate(ProbeRun run, bool resting)
    {
        var reading = run.Reading!;
        bool holds;
        try
        {
            holds = run.Probe.Holds(reading);
            run.Misread ??= reading.Misread;
        }
        catch (System.Exception ex)
        {
            Expect(false, $"{run.Type}: its probe's predicate threw {ex.GetType().Name}: {ex.Message}");
            run.HeldAfter = int.MaxValue;   // reported; stop asking
            return false;
        }

        if (resting && holds && !run.HeldAtRest)
        {
            run.HeldAtRest = true;
            Expect(false,
                   $"{run.Type} ('{run.Id}'): '{run.Probe.Effect}' already holds with nothing driven, "
                   + "so the probe cannot tell this part working from this part doing nothing");
        }
        return holds;
    }

    private void ReportProbes()
    {
        foreach (var run in _probes)
        {
            if (run.Misread is { } misread)
            {
                Expect(false,
                       $"{run.Type}: its probe reads {misread}. A predicate may only read the part's own "
                       + "inputs -- what it reports, not what the test wrote");
            }

            if (run.HeldAfter == int.MaxValue) continue;
            if (run.HeldAfter >= 0)
            {
                GD.Print($"  probe   {run.Type}: {Describe(run)} -> {run.Probe.Effect} "
                         + $"(after {run.HeldAfter} tick(s))");
                continue;
            }
            Expect(false,
                   $"{run.Type} ('{run.Id}'): {Describe(run)} did not produce '{run.Probe.Effect}' "
                   + $"within {run.Probe.WithinTicks} ticks. Nothing the part reports changed "
                   + "after the stimulus -- is its StepPart doing its job?");
        }
        GD.Print($"  probed {_probes.Count} part(s), exempt {ProbeExemptions.Count}");
    }

    private static string Describe(ProbeRun run)
    {
        var parts = new List<string>();
        if (run.Probe.Drive.Count > 0)
            parts.Add("driving " + string.Join(", ", run.Probe.Drive.Select(kv => $"{kv.Key}={kv.Value}")));
        if (run.Probe.Carton is { } carton)
            parts.Add($"a {(carton.Metal ? "metal" : carton.Tall ? "tall" : "short")} carton at {carton.At}");
        if (run.Probe.Operate.Count > 0)
            parts.Add("operating " + string.Join(" + ", run.Probe.Operate.Select(r => r.Length == 0 ? "(body)" : r)));
        if (run.Probe.Companion is { } companion)
            parts.Add($"a {companion.Type} companion at {companion.Offset}, driving "
                      + string.Join(", ", companion.Drive.Select(kv => $"{kv.Key}={kv.Value}")));
        return string.Join("; ", parts);
    }

    /// <summary>Every tag under one part's prefix, as suffix → number.</summary>
    private Dictionary<string, double> Snapshot(string id)
    {
        var values = new Dictionary<string, double>();
        foreach (var (suffix, tagId) in IdsUnder(id))
        {
            if (Tags.TryGetVisible(tagId, out var raw)) values[suffix] = ProbeReading.ToNumber(raw);
        }
        return values;
    }

    private Dictionary<string, string> IdsUnder(string id)
    {
        string prefix = id + ".";
        var ids = new Dictionary<string, string>();
        foreach (var tag in Tags)
        {
            if (tag.Id.StartsWith(prefix, System.StringComparison.Ordinal))
                ids[tag.Id.Substring(prefix.Length)] = tag.Id;
        }
        return ids;
    }
}
