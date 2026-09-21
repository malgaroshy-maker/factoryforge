using Godot;
using FactoryForge.TagBus;

namespace FactoryForge.Sim.DemoProfiles;

/// <summary>
/// The guarded cell. A transfer line whose motor is not commanded by the
/// controller at all — it is commanded by a contactor, and the contactor is
/// only allowed to pull in when two guarding devices say so.
///
/// Every other profile here writes the drive tag directly. This one never
/// writes <c>belt.rotate</c>; it writes <c>starter.coil</c>, and the starter
/// holds the motor. That one difference is the whole scene: it is what makes
/// "the relay closed" and "the machine started" two separate events, and it is
/// why this is the scene to open before writing a safety rung.
///
/// <b>A permissive is not a command.</b> The relay holds <c>starter.coil</c>
/// false while its safety outputs are open and <em>releases</em> it when they
/// close. Releasing a held tag starts nothing — it only hands the tag back to
/// the controller, which still has to decide to start. So closing the guards
/// and resetting the relay leaves the line exactly as stopped as it was, and
/// somebody has to press Start. A controller that treats "the relay closed" as
/// "go" has built automatic restart out of a relay that specifically refuses to
/// provide it.
///
/// <b>The mute is the operator's own foot-gun.</b> Cartons pass through the
/// scanner's protective field, so the field has to be bridged while they do,
/// and the panel's pot is how long the controller bridges it for. Turn it past
/// the scanner's own mute limit and consecutive cartons hold the mute
/// continuously — the scanner stops honouring it, the next carton trips the
/// field, and the line stops. That is what a mute without a limit would have
/// hidden, and <c>tools/try_scene.py --scene guarded-cell</c> measures it
/// rather than asserting it.
/// </summary>
public sealed class GuardedCellProfile : IDemoProfile
{
    /// <summary>Seconds between cartons. Wide enough that one carton is out of
    /// the protective field before the next one's mute starts, so a correctly
    /// sized mute window drops between cartons instead of being held
    /// continuously — which is the failure this scene exists to show, and not
    /// one the feed should cause by itself.</summary>
    private const float EmitHalfPeriod = 2.5f;

    /// <summary>Travel time from the push eye to the transfer station,
    /// seconds: 0.5 m of belt at 0.5 m/s. A distance and a speed, not a number
    /// somebody tuned until it looked right — and the reason the rod is fast,
    /// since the carton keeps moving underneath it for the whole stroke.
    /// </summary>
    private const float PushDelay = 1.0f;

    /// <summary>Where the pot sits if the scene has no panel at all.</summary>
    private const float DefaultMuteWindow = 3.5f;

    private enum Transfer { Idle, Waiting, Extending, Retracting }

    private readonly OperatorStation _station =
        new OperatorStation("panel", "belt.fault", "cylinder.fault");

    private Transfer _transfer;
    private float _pushTimer;
    private float _muteTimer;
    private float _emitTimer;
    private bool _emit;
    private bool _prevMuteEye, _prevPushEye;

    /// <summary>Latched by either guarding device dropping out. Cleared by
    /// Reset, and Reset alone does not restart anything.</summary>
    private bool _safetyTrip;

    /// <summary>
    /// A quarter of a second of held reset at the moment the demo starts — the
    /// commissioning press an engineer makes once, before handing the cell
    /// over.
    ///
    /// It exists because 🎬 Demo means "watch it run", and this is the one
    /// scene in the library that powers up with its motor held off by
    /// hardware: without a press, Demo would open on a still factory that is
    /// behaving perfectly. It is deliberately a fixed window and not a rule —
    /// a controller that re-pulsed the relay whenever the channels came back
    /// would have rebuilt the automatic restart the relay exists to refuse.
    /// After it expires, the only thing that pulses <c>relay.reset</c> is the
    /// operator's own Reset button. <c>tools/try_scene.py</c> has no
    /// equivalent: it presses Reset itself, and checks that skipping it leaves
    /// the line dead.
    /// </summary>
    private float _commissioningReset;

    public void Start(TagTable tags)
    {
        _station.Begin();
        _transfer = Transfer.Idle;
        _pushTimer = _muteTimer = _emitTimer = 0.0f;
        _emit = false;
        _prevMuteEye = _prevPushEye = false;
        _safetyTrip = false;
        _commissioningReset = 0.25f;

        OperatorStation.Set(tags, "starter.coil", false);
        OperatorStation.Set(tags, "cylinder.extend", false);
        OperatorStation.Set(tags, "cylinder.retract", false);
        OperatorStation.Set(tags, "scanner.mute", false);
        OperatorStation.Set(tags, "emitter.emit", false);
        _station.Lamps(tags);
    }

    public void Tick(double delta, TagTable tags)
    {
        float dt = (float)delta;

        bool commissioning = _commissioningReset > 0.0f;
        if (commissioning) _commissioningReset -= dt;
        bool resetPressed = OperatorStation.Bit(tags, "panel.reset") || commissioning;

        _station.Scan(tags);

        // --- the two guarding devices, read as verdicts ---------------------
        //
        // Neither of these is computed here. The relay decides whether its
        // contacts are closed and the scanner decides whether its field is
        // clear; a program that recomputed either from the raw channels would
        // be a second, unrated opinion about a safety function.
        bool relayClosed = OperatorStation.Bit(tags, "relay.k1")
                           && OperatorStation.Bit(tags, "relay.k2");
        bool fieldClear = !tags.Contains("scanner.stop") || OperatorStation.Bit(tags, "scanner.stop");

        // A guarding device dropping out stops the line and latches. Not a
        // momentary inhibit: a machine that restarted the instant the field
        // cleared would be the thing guarding exists to prevent. The
        // commissioning window is exempt because the relay powers up open and
        // has not been reset yet — that is not a trip, it is the state every
        // safety relay starts in.
        if (!commissioning && (!relayClosed || !fieldClear)) _safetyTrip = true;
        if (resetPressed && relayClosed && fieldClear) _safetyTrip = false;
        if (_safetyTrip) _station.HoldOff();

        // The relay's reset comes from the panel's Reset button, and from
        // nothing else. The relay itself takes the rising edge; holding this
        // high would achieve exactly one restart and no more, which is the
        // behaviour the part is written to guarantee.
        OperatorStation.Set(tags, "relay.reset", resetPressed);

        bool running = _station.Running && !_safetyTrip;

        // --- the motor, through its contactor -------------------------------
        //
        // `belt.rotate` is never written here. The starter holds it while the
        // contactor is in, and the auxiliary contact is what says the motor is
        // actually turning rather than merely commanded.
        OperatorStation.Set(tags, "starter.coil", running);
        bool motorRunning = OperatorStation.Bit(tags, "starter.aux");

        // --- muting ----------------------------------------------------------
        bool muteEye = OperatorStation.Bit(tags, "mute_eye.detect");
        if (muteEye && !_prevMuteEye && running)
            _muteTimer = (float)_station.Setpoint(tags, DefaultMuteWindow);
        _prevMuteEye = muteEye;

        if (_muteTimer > 0.0f) _muteTimer = Mathf.Max(_muteTimer - dt, 0.0f);
        OperatorStation.Set(tags, "scanner.mute", running && _muteTimer > 0.0f);

        // --- the feed ---------------------------------------------------------
        _emitTimer += dt;
        if (motorRunning)
        {
            if (_emitTimer >= EmitHalfPeriod) { _emit = !_emit; _emitTimer = 0.0f; }
        }
        else
        {
            _emit = false;
            _emitTimer = 0.0f;
        }
        OperatorStation.Set(tags, "emitter.emit", _emit);

        StepTransfer(dt, tags, motorRunning);

        // A guarding trip is a trip, and the panel has to say so. The shared
        // station only latches the mushroom and the drive faults, so the red
        // lamp is overridden here rather than left dark on the one stop this
        // scene exists to demonstrate. Mirrors tools/try_scene.py.
        _station.Lamps(tags);
        if (_safetyTrip)
        {
            OperatorStation.Set(tags, "panel.red", true);
            OperatorStation.Set(tags, "tower.red", true);
            OperatorStation.Set(tags, "tower.yellow", false);
        }
    }

    /// <summary>
    /// The transfer stroke, sequenced off the reeds rather than off a timer.
    ///
    /// Only one coil is ever energised. Energising both is not a way to hold a
    /// double-solenoid valve still — it is a way to leave the rod wherever the
    /// previous scan put it, which is the bug this cylinder exists to make
    /// visible. And "not extended" is never read as "retracted": each step
    /// waits for the reed that says where the rod actually is, because between
    /// them neither is made.
    /// </summary>
    private void StepTransfer(float dt, TagTable tags, bool motorRunning)
    {
        bool pushEye = OperatorStation.Bit(tags, "push_eye.detect");
        bool extended = OperatorStation.Bit(tags, "cylinder.extended");
        bool retracted = OperatorStation.Bit(tags, "cylinder.retracted");

        if (!motorRunning)
        {
            // Both coils dropped. The rod stays exactly where it stands — a
            // 5/2 valve has no spring — so an E-stop leaves the hazard
            // mid-stroke, which is the honest thing for it to do and the
            // reason the next start begins by bringing it home.
            OperatorStation.Set(tags, "cylinder.extend", false);
            OperatorStation.Set(tags, "cylinder.retract", false);
            _transfer = Transfer.Idle;
            _pushTimer = 0.0f;
            return;
        }

        if (pushEye && !_prevPushEye && _transfer == Transfer.Idle && retracted)
        {
            _transfer = Transfer.Waiting;
            _pushTimer = PushDelay;
        }
        _prevPushEye = pushEye;

        switch (_transfer)
        {
            case Transfer.Waiting:
                _pushTimer -= dt;
                if (_pushTimer <= 0.0f) _transfer = Transfer.Extending;
                break;

            case Transfer.Extending:
                if (extended) _transfer = Transfer.Retracting;
                break;

            case Transfer.Retracting:
                if (retracted) _transfer = Transfer.Idle;
                break;

            case Transfer.Idle:
                // A stroke interrupted by a stop leaves the rod between the
                // reeds. Bring it home before the next carton, and read the
                // reed rather than assuming.
                if (!retracted) _transfer = Transfer.Retracting;
                break;
        }

        bool extend = _transfer == Transfer.Extending;
        bool retract = _transfer == Transfer.Retracting;
        OperatorStation.Set(tags, "cylinder.extend", extend);
        OperatorStation.Set(tags, "cylinder.retract", retract);
    }
}
