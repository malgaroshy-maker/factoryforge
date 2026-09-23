using System;
using System.Collections.Generic;
using FactoryForge.TagBus;

namespace FactoryForge.Parts;

/// <summary>How an analog input reaches the controller (IP-16).</summary>
public enum AnalogSignalMode
{
    /// <summary>The measurement in engineering units -- percent, degC, L/min
    /// -- exactly as every analog input has always been published. The
    /// default, so no existing scene, template or grader model changes.</summary>
    Engineering,

    /// <summary>A raw S7 analog count, INT, 0..27648 across the measuring
    /// range. What a 0-10 V or 0-20 mA channel on an S7-1500 AI card hands the
    /// program, and what <c>NORM_X</c> / <c>SCALE_X</c> exist to undo.</summary>
    S7Raw,

    /// <summary>The same count from a 4-20 mA channel: 0 at 4 mA, 27648 at
    /// 20 mA. The live zero is what makes a broken wire detectable, so this
    /// mode, and only this mode, has an injectable wire break.</summary>
    Ma4To20,
}

/// <summary>
/// One analog input, published the way a real I/O card would publish it
/// (IP-16).
///
/// Every analog measurement in the library used to reach the PLC as a float in
/// engineering units, which skips the first thing a PLC student has to learn
/// about analog I/O: the program never sees "42.5 %", it sees an integer from
/// the card, and scaling it (<c>NORM_X</c> then <c>SCALE_X</c> on an S7) is the
/// program's job. A part opts in by holding one of these and routing its
/// declaration, its tick write, its reset write, its settings and its inspector
/// rows through it -- about one line each -- so the representation lives in
/// one place rather than being re-derived, slightly differently, in five
/// parts.
///
/// <b>The representation</b> is the S7-1500 analog value table for current
/// ranges (0-20 mA and 4-20 mA share it, only the physical current differs):
///
/// <code>
///   32767  7FFFh   overflow           (above 117.589 % of span)
///   32511..27649   overrange          (100.004 .. 117.589 %)
///   27648..0       nominal range      (100 .. 0 %)      20 mA .. 4 mA
///   -1..-4864      underrange         (-0.004 .. -17.593 %)  3.9995 .. 1.185 mA
///  -32768  8000h   underflow          (below -17.593 %)
/// </code>
///
/// <b>Wire break</b> (4-20 mA only) reads 32767 (7FFFh). That is what an
/// S7-1500 AI module reports on a 4-20 mA channel whose "wire break"
/// diagnostic is enabled: the current collapses to 0 mA, far below the 1.185 mA
/// the card can still represent, and the module substitutes 7FFFh and raises a
/// channel diagnostic. With that diagnostic disabled the same broken wire reads
/// as underflow (8000h) instead; this simulates the diagnostic-enabled
/// configuration, because that is the one a program is expected to handle.
/// The two ranges are worth distinguishing for exactly this reason: a broken
/// 0-20 mA wire reads 0, which is a perfectly valid "empty tank", and
/// <see cref="AnalogSignalMode.S7Raw"/> therefore has no wire break to inject.
///
/// The <c>&lt;id&gt;.wirebreak</c> tag is an <b>input</b>, following the
/// <c>.fault</c> precedent exactly: nothing in the simulation computes it, an
/// instructor or a test raises it by forcing it, and the controller reads it
/// like a sensor. Plays the part of the card's channel diagnostic (on an
/// S7-1500, the value status bit): the honest detection is still the value,
/// 32767, which is what a program should test.
///
/// On the bus a raw value is an <c>int</c> tag (32-bit, like everything
/// <c>int</c>) carrying a 16-bit INT's range. The count is rounded, not
/// truncated: a card quantises to the nearest step.
/// </summary>
public sealed class AnalogSignal
{
    /// <summary>100 % of the measuring range.</summary>
    public const int NominalFullScale = 27648;

    /// <summary>Top of the overrange; one count more is overflow.</summary>
    public const int OverrangeLimit = 32511;

    /// <summary>7FFFh: overflow, and on a 4-20 mA channel, wire break.</summary>
    public const int Overflow = 32767;

    /// <summary>Bottom of the underrange; one count less is underflow.</summary>
    public const int UnderrangeLimit = -4864;

    /// <summary>8000h: underflow.</summary>
    public const int Underflow = -32768;

    /// <summary>What a 4-20 mA channel reads with its wire cut.</summary>
    public const int WireBreakValue = Overflow;

    /// <summary>The <c>signal</c> setting's saved values, in
    /// <see cref="AnalogSignalMode"/> order. These strings are the scene-file
    /// format; the enum's names are not.</summary>
    private static readonly string[] ModeKeys = { "engineering", "s7_raw", "ma_4_20" };

    /// <summary>What the inspector's dropdown shows, same order.</summary>
    private static readonly string[] ModeLabels = { "Engineering", "S7 raw 0-27648", "4-20 mA raw" };

    private readonly float _sliderLow;
    private readonly float _sliderHigh;
    private readonly float _sliderStep;

    /// <param name="suffix">The measurement's own tag suffix, e.g.
    /// <c>"level"</c>.</param>
    /// <param name="rangeMin">Engineering value at 0 counts (4 mA).</param>
    /// <param name="rangeMax">Engineering value at 27648 counts (20 mA).</param>
    /// <param name="sliderLow">Lowest range end the inspector offers.</param>
    /// <param name="sliderHigh">Highest range end the inspector offers.</param>
    /// <param name="sliderStep">The inspector's step for both range ends.</param>
    /// <param name="engineeringIsInt">True for a measurement already published
    /// as an <c>int</c> in engineering units (the weighing conveyor's grams),
    /// so engineering mode stays exactly what it was.</param>
    /// <param name="wireBreakSuffix">The wire-break contact's suffix. Plain
    /// <c>wirebreak</c> for a part with one analog input, which is every part
    /// today; a part with two channels names each one's contact itself.</param>
    public AnalogSignal(string suffix, float rangeMin, float rangeMax,
                        float sliderLow, float sliderHigh, float sliderStep,
                        bool engineeringIsInt = false, string wireBreakSuffix = "wirebreak")
    {
        Suffix = suffix;
        WireBreakSuffix = wireBreakSuffix;
        RangeMin = rangeMin;
        RangeMax = rangeMax;
        _sliderLow = sliderLow;
        _sliderHigh = sliderHigh;
        _sliderStep = sliderStep;
        EngineeringIsInt = engineeringIsInt;
    }

    public string Suffix { get; }

    /// <summary>The injectable fault, declared only in
    /// <see cref="AnalogSignalMode.Ma4To20"/>.</summary>
    public string WireBreakSuffix { get; }

    public bool EngineeringIsInt { get; }

    public AnalogSignalMode Mode { get; set; } = AnalogSignalMode.Engineering;

    /// <summary>Engineering value at 0 counts. A setting.</summary>
    public float RangeMin { get; set; }

    /// <summary>Engineering value at 27648 counts. A setting.</summary>
    public float RangeMax { get; set; }

    public bool IsRaw => Mode != AnalogSignalMode.Engineering;

    /// <summary>Is a wire break being injected right now? Only meaningful in
    /// 4-20 mA mode, and only as of the last tick.</summary>
    public bool WireBroken { get; private set; }

    /// <summary>The last engineering value written, so a re-declaration made
    /// mid-run (the mode changed in the inspector) powers the new tag up at the
    /// live reading rather than at zero for one tick.</summary>
    private double _last;

    // ---------- the conversion

    /// <summary>
    /// Engineering value -> S7 analog count, with the S7-1500 overrange and
    /// underrange and their saturating codes (see the class summary).
    ///
    /// Pure and static so a test can hold it against its own arithmetic
    /// rather than against itself.
    /// </summary>
    public static int ToRaw(double engineering, double rangeMin, double rangeMax)
    {
        double span = rangeMax - rangeMin;
        // A range with no width -- or upside down -- is a misconfigured card,
        // not a division to attempt: an infinite count would be cast to int
        // and wrap. Report it the way the card reports anything it cannot
        // represent.
        if (!(span > 1e-9) || !double.IsFinite(engineering)) return Overflow;

        double counts = Math.Round(NominalFullScale * (engineering - rangeMin) / span,
                                   MidpointRounding.AwayFromZero);
        if (counts > OverrangeLimit) return Overflow;
        if (counts < UnderrangeLimit) return Underflow;
        return (int)counts;
    }

    /// <summary>What this channel would publish for
    /// <paramref name="engineering"/> right now, ignoring a wire break.</summary>
    public int Encode(double engineering) => ToRaw(engineering, RangeMin, RangeMax);

    // ---------- IPart plumbing, one call each

    /// <summary>Declare the measurement (and, at 4-20 mA, its wire break).
    ///
    /// Engineering mode declares exactly what the part always declared -- the
    /// same name, type and kind -- so the default changes nothing anywhere.
    /// The raw names do not mention the range: a name that moved with every
    /// drag of the Range slider would make the tag a different tag on every
    /// step, and re-announce the whole table each time.</summary>
    /// <param name="name">The measurement's name without its unit, e.g.
    /// <c>"Tank 1 Level"</c>.</param>
    /// <param name="unit">The engineering unit as it appears in the name, or
    /// null if the name never carried one.</param>
    public PartTagBuilder Declare(PartTagBuilder tags, string name, string? unit)
    {
        switch (Mode)
        {
            case AnalogSignalMode.S7Raw:
                return tags.Int(Suffix, $"{name} (S7 raw)", TagKind.Input, Encode(_last));
            case AnalogSignalMode.Ma4To20:
                return tags
                    .Int(Suffix, $"{name} (4-20 mA raw)", TagKind.Input, Encode(_last))
                    .Bit(WireBreakSuffix, $"{name} Wire Break", TagKind.Input);
            default:
                string engineeringName = unit is null ? name : $"{name} ({unit})";
                return EngineeringIsInt
                    ? tags.Int(Suffix, engineeringName, TagKind.Input, (int)_last)
                    : tags.Float(Suffix, engineeringName, TagKind.Input, _last);
        }
    }

    /// <summary>Publish this tick's measurement in whatever form the channel
    /// is configured for.</summary>
    public void Write(PartTick tick, double engineering)
    {
        _last = engineering;
        WireBroken = Mode == AnalogSignalMode.Ma4To20 && tick.Bit(WireBreakSuffix);

        if (!IsRaw)
        {
            if (EngineeringIsInt) tick.Write(Suffix, (int)engineering);
            else tick.Write(Suffix, engineering);
            return;
        }

        tick.Write(Suffix, WireBroken ? WireBreakValue : Encode(engineering));
    }

    /// <summary>The reset counterpart of <see cref="Write"/>. A reset writes
    /// through <see cref="TagTable.Set"/>, which throws on a type it cannot
    /// hold -- so a part in raw mode that still reset with <c>0.0</c> would
    /// throw mid-reset. Routing it here keeps the type right in every mode.
    /// </summary>
    public void Reset(PartReset reset, double engineering)
    {
        _last = engineering;
        if (!IsRaw)
        {
            if (EngineeringIsInt) reset.Write(Suffix, (int)engineering);
            else reset.Write(Suffix, engineering);
            return;
        }
        reset.Write(Suffix, Encode(engineering));
    }

    /// <summary>The three settings, always written -- a scene that says
    /// <c>engineering</c> out loud reads the same in any later build, whatever
    /// that build's default turns out to be.</summary>
    public void Capture(PartSettings settings)
    {
        settings.Put("signal", ModeKeys[(int)Mode]);
        settings.Put("range_min", RangeMin);
        settings.Put("range_max", RangeMax);
    }

    public void Apply(PartSettings settings)
    {
        if (settings.Text("signal") is { } key)
        {
            int index = Array.IndexOf(ModeKeys, key);
            // An unknown mode from a newer build keeps the default rather than
            // guessing: publishing counts a program is not expecting is worse
            // than publishing the engineering value it always got.
            if (index >= 0) Mode = (AnalogSignalMode)index;
        }
        if (settings.Number("range_min") is { } min) RangeMin = min;
        if (settings.Number("range_max") is { } max) RangeMax = max;
    }

    /// <summary>The inspector rows. The range is only offered once the channel
    /// is raw: in engineering mode it changes nothing, and a row that changes
    /// nothing is worse than no row (gotcha 11).</summary>
    public void Describe(IPartInspector ui)
    {
        ui.Choice("Signal", ModeLabels, (int)Mode, index => Mode = (AnalogSignalMode)index);
        if (!IsRaw) return;
        ui.Slider("Range Min", RangeMin, _sliderLow, _sliderHigh, _sliderStep, value => RangeMin = value);
        ui.Slider("Range Max", RangeMax, _sliderLow, _sliderHigh, _sliderStep, value => RangeMax = value);
    }

    /// <summary>The <c>signal</c> key's value for a mode, for tests and
    /// tooling that write scene files.</summary>
    public static string KeyFor(AnalogSignalMode mode) => ModeKeys[(int)mode];

    /// <summary>The dropdown label for a mode.</summary>
    public static string LabelFor(AnalogSignalMode mode) => ModeLabels[(int)mode];

    /// <summary>Every mode, in dropdown order.</summary>
    public static IReadOnlyList<AnalogSignalMode> Modes { get; } =
        new[] { AnalogSignalMode.Engineering, AnalogSignalMode.S7Raw, AnalogSignalMode.Ma4To20 };
}
