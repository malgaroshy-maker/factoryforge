using System;
using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// What a part does when it is driven, stated by the part so a generic test
/// can check it (IP-07).
///
/// <c>--self-test=scene</c> used to set every output of every part high, tick
/// twice and assert <c>Expect(true, …)</c>. The editor's dispatch catches a
/// part that throws, so that check could not fail at all: a part whose tick
/// had been deleted outright passed it. The probe replaces it. Each part says
/// three things —
///
/// <list type="bullet">
/// <item>a <b>stimulus</b>: outputs to drive (what a PLC would write), and
/// where a part cannot be driven that way, the physical thing that moves it —
/// a carton in its beam, a hand on its control, the machine it measures;</item>
/// <item>an <b>effect</b>, as a sentence for the failure message and as a
/// predicate over what the part <em>reports</em>: its own input tags, or for
/// a part that only shows something (a lamp, a needle), the state it renders;</item>
/// <item>how many physics ticks the effect may take.</item>
/// </list>
///
/// The test holds the part to it in two directions, and both matter. The
/// effect must be <em>absent</em> while nothing is driven — or the predicate
/// says nothing about the stimulus, and a part that ignores its tags would
/// still pass — and it must <em>appear</em> once the stimulus is applied. A
/// part whose <see cref="IPart.StepPart"/> does nothing fails the second half
/// by name.
///
/// Two rules keep a probe honest, and the test enforces both: it may only
/// drive tags the part declared as <see cref="TagKind.Output"/> (a probe that
/// writes its own feedback proves nothing), and its predicate may only read
/// tags declared as <see cref="TagKind.Input"/> (reading back the output the
/// test just wrote is the same tautology in another place).
///
/// Like <see cref="IPart.DeclareTags"/>, building a probe must not depend on
/// anything <c>_Ready</c> builds: the test reads the stimulus off a bare
/// instance to lay out the scene. The predicate is only ever called on the
/// placed, ready part.
/// </summary>
public sealed class PartProbe
{
    public PartProbe(string effect, Func<ProbeReading, bool> holds)
    {
        Effect = effect;
        Holds = holds;
    }

    /// <summary>What should happen, in words: the failure message is
    /// "&lt;part&gt;: driving … did not produce &lt;Effect&gt;".</summary>
    public string Effect { get; }

    /// <summary>True once the effect is there. Called every tick.</summary>
    public Func<ProbeReading, bool> Holds { get; }

    /// <summary>Output suffix → value, written once, the way a PLC's first scan
    /// after a rung goes true would. The value's type must suit the tag: a
    /// <c>double</c> for a float, an <c>int</c> for an int.</summary>
    public IReadOnlyDictionary<string, object> Drive { get; init; } = new Dictionary<string, object>();

    /// <summary>A carton parked in the part's working space, for a part that
    /// senses product rather than being commanded.</summary>
    public ProbeCarton? Carton { get; init; }

    /// <summary>Regions to operate once, as a click in Run mode would, for a
    /// part whose inputs a person drives by hand. The empty string is a
    /// whole-body click.</summary>
    public IReadOnlyList<string> Operate { get; init; } = Array.Empty<string>();

    /// <summary>A second part the effect needs, for a part that measures
    /// another machine: an encoder has to ride a belt, a flow meter has to have
    /// a pump to meter.</summary>
    public ProbeCompanion? Companion { get; init; }

    /// <summary>Physics ticks (60 a second) the effect may take to appear. A
    /// bound, not a target: the test passes on the first tick it holds.</summary>
    public int WithinTicks { get; init; } = 60;

    /// <summary><c>Drive = PartProbe.Drives(("run", true), ("speed", 100.0))</c>.</summary>
    public static IReadOnlyDictionary<string, object> Drives(params (string Suffix, object Value)[] values)
    {
        var drive = new Dictionary<string, object>();
        foreach (var (suffix, value) in values) drive[suffix] = value;
        return drive;
    }
}

/// <param name="At">Where the carton's bottom-centre goes, in the part's own
/// frame. The test raises it by half the carton's height and parks it frozen,
/// kinematic, so it stays put across the ticks the physics server needs to
/// notice it rather than falling out of the beam first.</param>
public sealed record ProbeCarton(Vector3 At, bool Tall = true, bool Metal = false);

/// <param name="Type">A catalog type name.</param>
/// <param name="Offset">Where it goes, in the probed part's frame.</param>
/// <param name="Drive">Outputs of the companion to drive, by suffix.</param>
public sealed record ProbeCompanion(string Type, Vector3 Offset, IReadOnlyDictionary<string, object> Drive);

/// <summary>
/// A probed part's own tags, as its predicate sees them. Reads are by suffix,
/// like <see cref="PartTick"/>'s, and only <see cref="TagKind.Input"/> tags
/// count: the first read of anything else is recorded, and the test fails the
/// probe for it.
/// </summary>
public sealed class ProbeReading
{
    private readonly TagTable _tags;
    private readonly string _instanceId;
    private readonly IReadOnlyDictionary<string, double> _atStart;

    public ProbeReading(TagTable tags, string instanceId, IReadOnlyDictionary<string, double> atStart)
    {
        _tags = tags;
        _instanceId = instanceId;
        _atStart = atStart;
    }

    /// <summary>The first tag the predicate read that it had no business
    /// reading — an output, or a suffix the part does not have. Null when every
    /// read was of one of the part's own inputs.</summary>
    public string? Misread { get; private set; }

    public bool Bit(string suffix) => Number(suffix) != 0.0;

    /// <summary>An input's current value, bits as 0 and 1.</summary>
    public double Number(string suffix)
    {
        string id = $"{_instanceId}.{suffix}";
        var tag = _tags.Get(id);
        if (tag is null || tag.Kind != TagKind.Input)
        {
            Misread ??= tag is null ? $"'{suffix}' (no such tag)" : $"'{suffix}' (an output)";
            return 0.0;
        }
        return ToNumber(_tags.Visible(id));
    }

    /// <summary>The same input's value when this phase began — at rest, the
    /// start of the rest; once driven, the moment the stimulus was
    /// applied.</summary>
    public double AtStart(string suffix)
    {
        Number(suffix);   // the same rule for what may be read
        return _atStart.TryGetValue(suffix, out double value) ? value : 0.0;
    }

    public static double ToNumber(object raw) => raw switch
    {
        bool b => b ? 1.0 : 0.0,
        _ => Convert.ToDouble(raw),
    };
}
