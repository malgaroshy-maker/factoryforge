using Godot;
using FactoryForge.TagBus;

namespace FactoryForge.Sim.DemoProfiles;

/// <summary>
/// A batch dosing station: two loops, one inside the other, and a batch that
/// ends on a quantity rather than on a clock.
///
/// The library's analog scenes were a tank with its own fill valve and a hot
/// plate with an element — both single-loop, both controlling the thing they
/// directly actuate. A pump changes that, because the pump's speed reference is
/// not what it delivers. What it delivers is a <em>flow</em>, a measurement in
/// its own right, and it moves in a second where a tank level moves in a
/// minute. That separation is the whole precondition for a cascade, and until
/// there was a pump and a flow meter there was nothing here to cascade onto.
///
/// <b>The outer loop sets the inner loop's setpoint.</b> The batch controller
/// watches the totaliser and asks for a dosing rate; the flow controller trims
/// <c>pump.speed</c> until <c>meter.rate</c> is that rate. The outer loop tapers
/// its demand over the last few litres — a full-rate dose that slams shut
/// overshoots the number, and a creep-feed approach is how every real batch
/// hits one.
///
/// <b>The batch ends on litres, not on seconds.</b> Halve the dosing rate and
/// the same recipe takes twice as long and delivers the same amount; a batch
/// timed in seconds would have delivered half of it. <c>tools/try_scene.py
/// --scene batch-dosing</c> runs exactly that comparison and prints both
/// numbers.
///
/// <b>The inner loop is the alarm.</b> A failed pump goes on reporting the
/// speed it was given and delivers nothing. The level will eventually say so,
/// minutes later; the flow measurement says so in a second, which is the other
/// reason to close a loop around it.
/// </summary>
public sealed class BatchDosingProfile : IDemoProfile
{
    /// <summary>Normal dosing rate, litres per minute. Just under the pump's
    /// rating, so the inner loop has somewhere to go when it needs more.</summary>
    private const float DoseRate = 110.0f;

    /// <summary>The approach. Over the last few litres the outer loop tapers
    /// its demand rather than running flat out into the cut-off — a valve
    /// slammed shut at full rate carries its own overshoot past the
    /// number.</summary>
    private const float CreepLitres = 4.0f;
    private const float CreepFloor = 0.25f;

    /// <summary>The inner flow loop. Percent of pump speed per litre/minute of
    /// error, and per litre/minute-second of it. Mostly integral: the steady
    /// speed that holds a flow is not proportional to any error, so a P-only
    /// flow controller parks below setpoint for the same reason the heat treat
    /// station's does.</summary>
    private const float Kp = 0.25f;
    private const float Ki = 1.0f;

    /// <summary>No-flow detection, on the inner loop. The pump is being asked
    /// for half speed or more and the meter reads almost nothing: that is a dry
    /// pump, a shut manual valve or a burst line, and it is the diagnosis the
    /// level cannot make for another minute.</summary>
    private const float NoFlowSeconds = 2.0f;
    private const float NoFlowFraction = 0.3f;

    private const float DefaultBatch = 20.0f;

    private enum Phase { Zeroing, Dosing, Transferring, Complete }

    private readonly OperatorStation _station = new OperatorStation("panel");

    private Phase _phase;
    private float _speed;
    private float _integral;
    private float _dryFor;
    private float _levelAtStart;
    private bool _noFlow;

    public void Start(TagTable tags)
    {
        _station.Begin();
        _phase = Phase.Zeroing;
        _speed = _integral = _dryFor = _levelAtStart = 0.0f;
        _noFlow = false;

        OperatorStation.Set(tags, "pump.run", false);
        OperatorStation.Set(tags, "pump.speed", 0.0);
        OperatorStation.Set(tags, "tank.fill", 0.0);
        OperatorStation.Set(tags, "tank.drain", 0.0);
        _station.Lamps(tags);
    }

    public void Tick(double delta, TagTable tags)
    {
        float dt = (float)delta;

        bool resetPressed = OperatorStation.Bit(tags, "panel.reset");
        _station.Scan(tags);
        if (resetPressed) _noFlow = false;
        if (_noFlow) _station.HoldOff();

        float target = (float)_station.Setpoint(tags, DefaultBatch);
        float total = (float)OperatorStation.Num(tags, "meter.total");
        float rate = (float)OperatorStation.Num(tags, "meter.rate");
        float level = (float)OperatorStation.Num(tags, "tank.level");

        // Start on a finished batch starts the next one. Taken before the
        // phase machine runs, so the Complete case does not stop the line on
        // the same scan the operator asked for another batch.
        if (_station.StartEdge && _phase == Phase.Complete && !_noFlow) _phase = Phase.Zeroing;

        bool running = _station.Running && !_noFlow;
        bool zeroing = false, dosing = false, draining = false;

        switch (_phase)
        {
            case Phase.Zeroing:
                // Hold the totaliser's reset and wait for it to actually read
                // zero. It is a level, not an edge, so holding it is how you
                // zero it — and waiting for the readback is how you know the
                // batch is counting from zero rather than from whatever the
                // last one left.
                zeroing = true;
                if (running && total <= 0.0f)
                {
                    _phase = Phase.Dosing;
                    _levelAtStart = level;
                    _integral = 0.0f;
                }
                break;

            case Phase.Dosing:
                // A Stop mid-dose suspends; Start resumes the same batch,
                // because the totaliser kept the litres already delivered.
                dosing = running;
                if (total >= target) _phase = Phase.Transferring;
                break;

            case Phase.Transferring:
                // The batch goes downstream. Without this the tank fills up
                // over a shift and the station has nowhere to put batch eight.
                draining = running;
                if (level <= _levelAtStart + 0.5f) _phase = Phase.Complete;
                break;

            case Phase.Complete:
                _station.HoldOff();
                break;
        }

        // --- the inner loop -------------------------------------------------
        float flowSetpoint = 0.0f;
        if (dosing)
        {
            float remaining = Mathf.Max(target - total, 0.0f);
            float taper = remaining >= CreepLitres
                ? 1.0f
                : Mathf.Max(remaining / CreepLitres, CreepFloor);
            flowSetpoint = DoseRate * taper;

            float error = flowSetpoint - rate;
            // Integrate only off the stops. Winding up against a saturated
            // pump is the same mistake the heat treat station's controller
            // avoids, and here it would carry the batch straight past its
            // number on the way back down.
            if (_speed > 0.5f && _speed < 99.5f)
                _integral = Mathf.Clamp(_integral + error * Ki * dt, -100.0f, 100.0f);
            _speed = Mathf.Clamp(error * Kp + _integral, 0.0f, 100.0f);
        }
        else
        {
            _speed = 0.0f;
            _integral = 0.0f;
        }

        // --- no flow, seen from the inner loop -------------------------------
        if (dosing && _speed > 50.0f && rate < flowSetpoint * NoFlowFraction) _dryFor += dt;
        else _dryFor = 0.0f;

        if (_dryFor >= NoFlowSeconds)
        {
            _noFlow = true;
            _dryFor = 0.0f;
            GD.Print("batch-dosing: no flow — the pump is being commanded and the meter "
                     + "reads nothing");
        }

        OperatorStation.Set(tags, "meter.reset", zeroing);
        OperatorStation.Set(tags, "pump.run", dosing);
        OperatorStation.Set(tags, "pump.speed", (double)_speed);
        OperatorStation.Set(tags, "tank.fill", 0.0);
        OperatorStation.Set(tags, "tank.drain", draining ? 100.0 : 0.0);
        OperatorStation.Set(tags, "flow_gauge.value", (double)rate);
        OperatorStation.Set(tags, "total_display.value", Mathf.RoundToInt(total));
        OperatorStation.Set(tags, "level_readout.value", Mathf.RoundToInt(level));

        _station.Lamps(tags);
        if (_noFlow)
        {
            OperatorStation.Set(tags, "panel.red", true);
            OperatorStation.Set(tags, "tower.red", true);
            OperatorStation.Set(tags, "tower.yellow", false);
        }
    }
}
