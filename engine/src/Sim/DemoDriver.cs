using System;
using System.Collections.Generic;
using Godot;
using FactoryForge.Sim.DemoProfiles;
using FactoryForge.TagBus;

namespace FactoryForge.Sim;

/// <summary>
/// An in-engine stand-in for a PLC, so the app demonstrates itself with
/// nothing installed. A first launch used to sit perfectly still — nothing
/// writes <c>conveyor.rotate</c> or <c>emitter.emit</c> unless a sidecar is
/// running, and the app shipped no way to see it move without one — which
/// reads as "this is broken" in the first thirty seconds. See FF-23.
///
/// Looks up a small <see cref="IDemoProfile"/> by the loaded scene's name and
/// runs it, rather than hardcoding one scene's logic here directly (UX-16) —
/// every shipped scene gets its own honest exercise (docs/UX_PLAN.md §4)
/// instead of the sorting line's logic silently no-op'ing on everything else.
///
/// Stands down the instant a real driver connects — it must never fight a
/// PLC for the same tags, so <see cref="Bus"/> is checked every frame rather
/// than trusting whoever started this to also remember to stop it.
/// </summary>
public partial class DemoDriver : Node
{
    public TagTable Tags { get; set; } = null!;
    public TagBusServer Bus { get; set; } = null!;

    /// <summary>Raised when the demo starts or stops, including the automatic
    /// stand-down on a real driver connecting — so the toolbar toggle and the
    /// idle hint both follow the true state instead of only what they were
    /// told.</summary>
    [Signal] public delegate void ActiveChangedEventHandler(bool active);

    /// <summary>Raised instead of <see cref="ActiveChanged"/> when
    /// <see cref="Start"/> refuses because the loaded scene has no profile —
    /// a distinct signal rather than a state a UI has to poll for staleness,
    /// so pressing Demo twice on the same broken scene tells you twice (UX-30).</summary>
    [Signal] public delegate void RefusedEventHandler(string reason);

    public bool Active { get; private set; }

    /// <summary>The reason the most recent <see cref="Start"/> refused, or
    /// null if it did not. Kept for anything that wants the current state
    /// rather than the moment-of-refusal event <see cref="Refused"/> carries.</summary>
    public string? RefusalReason { get; private set; }

    private static readonly Dictionary<string, Func<IDemoProfile>> Profiles = new()
    {
        ["sorting-by-height"] = () => new SortingByHeightProfile(),
        ["start-stop-station"] = () => new StartStopStationProfile(),
        ["tank-level-control"] = () => new TankLevelControlProfile(),
        ["light-curtain-sorting"] = () => new LightCurtainSortingProfile(),
        ["roller-line-weighing"] = () => new RollerLineWeighingProfile(),
        ["pick-and-place-cell"] = () => new PickAndPlaceCellProfile(),
        ["heat-treat-station"] = () => new HeatTreatStationProfile(),
        ["accumulation-buffer"] = () => new AccumulationBufferProfile(),
        ["guarded-cell"] = () => new GuardedCellProfile(),
        ["batch-dosing"] = () => new BatchDosingProfile(),
        ["palletising-cell"] = () => new PalletisingCellProfile(),
    };

    private IDemoProfile? _profile;

    public void Start()
    {
        if (Active) return;

        string scene = Bus.SceneName;
        if (!Profiles.TryGetValue(scene, out var makeProfile))
        {
            RefusalReason = $"no built-in exercise for scene '{scene}'";
            GD.Print($"Demo: {RefusalReason}");
            EmitSignal(SignalName.Refused, RefusalReason);
            return;
        }

        RefusalReason = null;
        // A fresh instance every start, not a reused one: a profile's edge
        // detectors and timers are only valid from the moment it saw the
        // scene's initial state, and Stop() deliberately does not rewind them.
        _profile = makeProfile();
        Active = true;
        _profile.Start(Tags);
        EmitSignal(SignalName.ActiveChanged, true);
    }

    public void Stop()
    {
        if (!Active) return;
        Active = false;
        _profile = null;
        // Leave outputs where they are. A real driver about to take over
        // writes them itself; snapping a running belt to "off" the instant a
        // PLC connects would look like a fault at the exact moment the
        // handoff is supposed to be invisible.
        EmitSignal(SignalName.ActiveChanged, false);
    }

    // Physics clock, not the frame clock: a panel button's rising edge is
    // exactly one _PhysicsProcess tick wide (SceneEditor.StepPanelButtons sets
    // it, then clears it at the top of the next physics tick), and headless
    // has no vsync holding _Process and _PhysicsProcess at the same cadence --
    // uncapped, the engine can run several physics ticks per frame to catch
    // up. A frame-clock reader can watch a pulse turn on and off again between
    // two of its own calls and never see it high at all. Found this the hard
    // way writing UX-17's self-test: pressing Start could be silently
    // swallowed, reproducibly, right after a template load's own allocation
    // hitch gave the catch-up loop something to catch up on. SceneEditor is
    // added to the tree before DemoDriver, so this still reads each tick's
    // writes in the same order _Process did, just on the clock that cannot
    // skip past them.
    public override void _PhysicsProcess(double delta)
    {
        if (!Active || _profile is null) return;

        if (Bus.HasClient)
        {
            Stop();
            return;
        }

        _profile.Tick(delta, Tags);
    }
}
