using System;
using System.Collections.Generic;
using FactoryForge.TagBus;

namespace FactoryForge.Parts;

/// <summary>
/// An analog <em>output</em> -- a command the PLC writes to an actuator --
/// taken the way a real AO card delivers it (IP-28). The counterpart of
/// <see cref="AnalogSignal"/>, which does the same for measurements.
///
/// By default an analog output is what it always was: a float in the
/// actuator's own units, a valve opening in percent that a PID can write
/// without scaling. Switched to <c>s7_raw</c> or <c>ma_4_20</c> the tag becomes
/// an INT the program writes counts into -- 0..27648 across the span, exactly
/// what an S7 program hands a <c>%QW</c> after <c>NORM_X</c> and
/// <c>SCALE_X</c> -- and the part turns the counts back into the opening, the
/// power or the speed the actuator actually produces.
///
/// <b>What the card does with a count</b> follows the S7-1500 analog value
/// tables for output ranges (0-10 V, 0-20 mA and 4-20 mA share their counts):
///
/// <code>
///   &gt; 32511          overflow    output switched off: 0 V / 0 mA
///   32511..27649     overrange   up to 117.6 % -- 11.76 V, 23.52 / 22.81 mA
///   27648..0         nominal     100 .. 0 %     10 V / 20 mA .. 0 V / 0 or 4 mA
///   -1..-6912        underrange  4-20 mA only, 3.9995 .. 0 mA; a unipolar
///                                0-10 V / 0-20 mA output stops at 0
///   below            limited to 0 mA, and past -32512 underflow: 0 mA
/// </code>
///
/// <b>What the actuator does with the current</b> is the part's business, and
/// every actuator in the library is a thing with two end stops: a valve cannot
/// open more than fully open, a heater's power controller cannot deliver more
/// than its rating, a drive clamps at its maximum speed, a gauge's needle
/// hits the peg. So the overrange drives the actuator to the top of its span
/// and no further, and anything at or below 0 counts leaves it at the bottom.
/// The overflow codes (32512 and up, 7FFFh included) are the one surprise and
/// they are real: the card switches the channel off, the actuator loses its
/// signal and sits at the bottom of its span -- a program that forgot to clamp
/// before writing 40000 into a <c>%QW</c> gets a <em>shut</em> valve, not a full
/// one. That is the lesson, and it is why the conversion is not a plain clamp.
///
/// <b>No wire break.</b> A broken wire on a 4-20 mA <em>output</em> is detected
/// by the card and reported as a channel diagnostic (on an S7-1500, through the
/// diagnostic buffer, an interrupt, or the module's value-status bits in the
/// input image) -- never as a value in the output word the program writes. The
/// input side's wire break exists because the broken wire changes the number
/// the program reads; here there is no number for it to change, and the
/// physical effect -- the actuator losing its signal -- is already what each
/// of these parts' <c>.fault</c> contact models. So <c>s7_raw</c> and
/// <c>ma_4_20</c> take the same counts and differ only in the tag's name,
/// which says how the channel was wired: that is also true of the real card.
///
/// <b>The span is not a setting of its own.</b> A transmitter's range is
/// configured in the transmitter, so <see cref="AnalogSignal"/> offers one. An
/// actuator's span is its full scale, and every actuator here already has
/// that as a setting: 20 mA is a fully open valve, full heater power, 100 % of
/// the pump's rated flow or the drive's maximum speed, the far end of the
/// gantry's rail, the top of the gauge's plate. A second pair of sliders
/// would be a second place to say the same thing, and could disagree.
///
/// One instance can serve several outputs of the same part -- the tank's two
/// valves are the same positioner on the same card, and share one setting --
/// so every per-tag call names its suffix.
/// </summary>
public sealed class AnalogOutput
{
    /// <summary>The scene-file key. Not <c>signal</c>: that one belongs to the
    /// part's analog <em>input</em> (IP-16), and a pump has both.</summary>
    public const string SettingKey = "output_signal";

    /// <summary>The inspector row's label, for the same reason.</summary>
    public const string Label = "Output Signal";

    private readonly Func<(float Min, float Max)> _range;

    /// <summary>The last engineering value read per suffix, so a re-declaration
    /// made mid-run powers the new tag up at the live command rather than
    /// slamming the actuator to zero -- and so a click can toggle from where
    /// the actuator actually is.</summary>
    private readonly Dictionary<string, double> _last = new();

    /// <param name="rangeMin">Engineering value at 0 counts.</param>
    /// <param name="rangeMax">Engineering value at 27648 counts.</param>
    public AnalogOutput(float rangeMin = 0.0f, float rangeMax = 100.0f)
        : this(() => (rangeMin, rangeMax)) { }

    /// <summary>For an actuator whose span is another setting of the part: the
    /// gauge, whose plate is what 4 and 20 mA mean.</summary>
    public AnalogOutput(Func<(float Min, float Max)> range) => _range = range;

    public AnalogSignalMode Mode { get; set; } = AnalogSignalMode.Engineering;

    public bool IsRaw => Mode != AnalogSignalMode.Engineering;

    public float RangeMin => _range().Min;
    public float RangeMax => _range().Max;

    // ---------- the conversion

    /// <summary>
    /// A count the PLC wrote -> what the actuator does, per the table in the
    /// class summary. Pure and static so a test can hold it against its own
    /// arithmetic.
    /// </summary>
    public static double ToEngineering(long counts, double rangeMin, double rangeMax)
    {
        // Overflow: the card switches the channel off, the actuator loses its
        // signal. Checked first -- it is the one place a bigger number gives a
        // smaller result.
        if (counts > AnalogSignal.OverrangeLimit) return rangeMin;
        // 0 counts is the bottom of the span, and every count below it is
        // still "below the bottom" to an actuator: a unipolar output cannot go
        // negative at all, and 4-20 mA's underrange is less than 4 mA.
        if (counts <= 0) return rangeMin;
        // Overrange is real current past 20 mA, and the actuator is at its
        // end stop.
        if (counts >= AnalogSignal.NominalFullScale) return rangeMax;
        return rangeMin + (rangeMax - rangeMin) * counts / (double)AnalogSignal.NominalFullScale;
    }

    /// <summary>
    /// An engineering command -> the count that produces it, rounded to the
    /// nearest step and kept inside the nominal range. For the engine's own
    /// writes to an output -- a click on the part, the IP-07 probe -- which
    /// should command what they mean and nothing a card would treat as a code.
    /// </summary>
    public static int ToCounts(double engineering, double rangeMin, double rangeMax)
    {
        double span = rangeMax - rangeMin;
        if (!(span > 1e-9) || !double.IsFinite(engineering)) return 0;
        double counts = Math.Round(AnalogSignal.NominalFullScale * (engineering - rangeMin) / span,
                                   MidpointRounding.AwayFromZero);
        return (int)Math.Clamp(counts, 0.0, AnalogSignal.NominalFullScale);
    }

    // ---------- IPart plumbing, one call each

    /// <summary>
    /// Declare one output. Engineering mode declares exactly what the part
    /// always declared -- a float output with the unit in its name -- so the
    /// default changes nothing anywhere.
    /// </summary>
    /// <param name="name">The command's name without its unit, e.g.
    /// <c>"Tank 1 Fill Valve"</c>.</param>
    /// <param name="unit">The unit as the engineering name shows it, or null
    /// if the name never carried one.</param>
    /// <param name="initial">Where the part powers up in engineering units
    /// (the arm's elbow starts at 90 degrees, a valve at 0 %).</param>
    public PartTagBuilder Declare(PartTagBuilder tags, string suffix, string name, string? unit,
                                  double initial = 0.0)
    {
        double live = _last.TryGetValue(suffix, out var last) ? last : initial;
        return Mode switch
        {
            AnalogSignalMode.S7Raw =>
                tags.Int(suffix, $"{name} (S7 raw)", TagKind.Output, ToCounts(live, RangeMin, RangeMax)),
            AnalogSignalMode.Ma4To20 =>
                tags.Int(suffix, $"{name} (4-20 mA raw)", TagKind.Output, ToCounts(live, RangeMin, RangeMax)),
            _ => tags.Float(suffix, unit is null ? name : $"{name} ({unit})", TagKind.Output, live),
        };
    }

    /// <summary>This tick's command in engineering units, whatever form the
    /// PLC wrote it in. A missing tag reads as the bottom of the span, the
    /// way <see cref="PartTick.Number"/> reads it as zero.</summary>
    public double Read(PartTick tick, string suffix)
    {
        double value;
        if (!IsRaw)
        {
            value = tick.Number(suffix);
        }
        else
        {
            value = tick.TryRead(suffix, out var raw)
                ? ToEngineering(ToWhole(raw), RangeMin, RangeMax)
                : RangeMin;
        }
        _last[suffix] = value;
        return value;
    }

    /// <summary>The value to force for an engineering command: the command
    /// itself by default, the count that produces it in raw mode. For the
    /// part's own clicks and its IP-07 probe, which would otherwise hand an
    /// INT tag a double and be refused.</summary>
    public object Command(double engineering) =>
        // Both arms boxed explicitly. `IsRaw ? int : double` is a double
        // expression in C#, so the count would leave here as 27648.0 -- the
        // very write the INT tag refuses. The first version did exactly that.
        IsRaw ? (object)ToCounts(engineering, RangeMin, RangeMax) : (object)engineering;

    /// <summary>
    /// A click on an analog actuator: fully on or fully off, as
    /// <see cref="PartOperate.ToggleAnalog"/> does for a percent, but in
    /// whatever form the output is configured for. Judged from what the tag
    /// reads now, not from the part's last tick, so two clicks between ticks
    /// are still an open and a close.
    /// </summary>
    public void Toggle(PartOperate op, string suffix)
    {
        double current = op.TryNumber(suffix, out double raw)
            ? (IsRaw ? ToEngineering((long)Math.Round(raw), RangeMin, RangeMax) : raw)
            : RangeMin;
        // "Open at all" rather than "past half way", as ToggleAnalog has it:
        // a valve a PID has cracked to 3 % is open, and a click closes it.
        double threshold = RangeMin + 0.005 * (RangeMax - RangeMin);
        op.Force(suffix, Command(current > threshold ? RangeMin : RangeMax));
    }

    public void Capture(PartSettings settings) =>
        settings.Put(SettingKey, AnalogSignal.KeyFor(Mode));

    public void Apply(PartSettings settings)
    {
        // Unknown from a newer build: keep the default, for the reason
        // AnalogSignal.Apply gives.
        if (AnalogSignal.ParseKey(settings.Text(SettingKey)) is { } mode) Mode = mode;
    }

    /// <summary>One row: the signal type. No range rows -- see the class
    /// summary.</summary>
    public void Describe(IPartInspector ui) =>
        ui.Choice(Label, AnalogSignal.Labels, (int)Mode, index => Mode = (AnalogSignalMode)index);

    /// <summary>A tag's value as a whole count. A raw output is an INT, but a
    /// tag caught between a mode change and its re-declaration can still hold
    /// the old float; rounding it is the honest reading.</summary>
    private static long ToWhole(object raw) => raw switch
    {
        int i => i,
        long l => l,
        // Far outside any card's range either way; clamped so the cast is
        // defined.
        double d when double.IsFinite(d) => (long)Math.Round(Math.Clamp(d, -1e12, 1e12)),
        _ => 0,
    };
}
