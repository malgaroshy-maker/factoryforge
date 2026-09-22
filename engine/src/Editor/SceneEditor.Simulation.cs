using System.Collections.Generic;
using FactoryForge.Parts;
using FactoryForge.Scenes;
using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
    /// <summary>Below this, a carton has fallen off the world and is not
    /// coming back: a live RigidBody3D with ContinuousCd doing broad- and
    /// narrow-phase work every physics step, forever, since the only despawn
    /// paths were a remover zone and Reset. See FF-12.</summary>
    private const float KillPlaneY = -2.0f;

    /// <summary>Sweeping every tick would be wasted work for something that
    /// only needs to be noticed within about a second.</summary>
    private const float KillPlaneIntervalSeconds = 1.0f;

    /// <summary>Live cartons above this pause the emitter and raise a
    /// warning, rather than an unattended scene accumulating rigid bodies
    /// without bound. Not a hard limit on anything already alive — cartons
    /// already on the belt are left to reach a remover normally.</summary>
    public const int LiveItemCap = 200;

    private float _killPlaneAccumulator;

    /// <summary>As of the last sweep (at most <see cref="KillPlaneIntervalSeconds"/>
    /// stale) — good enough for a status chip, not a physics guarantee.</summary>
    public int LiveItemCount { get; private set; }
    public bool ItemCapHit { get; private set; }

    public override void _PhysicsProcess(double delta)
    {
        if (Tags is null) return;
        float dt = (float)delta;

        _killPlaneAccumulator += dt;
        if (_killPlaneAccumulator >= KillPlaneIntervalSeconds)
        {
            _killPlaneAccumulator = 0f;
            SweepBoxes();
        }

        // Every part drives itself (HP-34). This was four hundred lines of
        // `switch (part.PartType)` with one arm per machine, in the file
        // furthest from every one of them -- so a tag registered in
        // PartTagManager and never dispatched here was a tag that existed,
        // appeared in the inspector, could be forced and did nothing, which is
        // exactly what happened to the weighing conveyor's fault contact.
        foreach (var part in _placedParts)
        {
            if (part.Node is not IPart driven) continue;

            // One part's throw must not take the rest of the scene down with
            // it. Godot logs an exception out of _PhysicsProcess and carries on
            // (gotcha 12), but the carrying-on happens *outside* this loop --
            // so a single misbehaving part silently stopped every part placed
            // after it, on every tick, while the log filled with one stack
            // trace per frame.
            //
            // The nearest live example is a part that computes a non-finite
            // float: TagTable.Set rejects those rather than storing them
            // (HP-23), so a division that has gone to infinity surfaces here
            // rather than as a wrong number on the bus. Guard the arithmetic in
            // the part; this is what catches the one that got away, and it says
            // which part it was.
            try
            {
                driven.StepPart(new PartTick(Tags, part.TagIds, part.InstanceId, dt, this));
            }
            catch (System.Exception ex)
            {
                // Once per part, not once per tick: at 60 Hz the second form is
                // a 23 MB log and a window that looks hung, which is exactly
                // how gotcha 21 presented.
                if (_brokenParts.Add(part.InstanceId))
                    GD.PushError($"part '{part.InstanceId}' threw during its tick; the rest of "
                                 + $"the scene is still running, this part is not: {ex}");
            }
        }
    }

    /// <summary>Parts that have thrown once. See the catch in
    /// <see cref="_PhysicsProcess"/>.</summary>
    private readonly HashSet<string> _brokenParts = new();

    // ---------- IPartHost: the little a part legitimately asks of the scene

    /// <summary>Is there room under the live-carton cap for one more?</summary>
    bool IPartHost.CanSpawnItem => LiveItemCount < LiveItemCap;

    /// <summary>Tall, short, tall, short. Shared across every emitter in the
    /// scene, so a line fed by two of them still gets a mixed stream rather
    /// than two independent ones that happen to agree.</summary>
    bool IPartHost.NextAlternate()
    {
        bool value = _emitAlternate;
        _emitAlternate = !_emitAlternate;
        return value;
    }

    /// <summary>The deterministic scene transports its boxes in code and
    /// mirrors one named belt, so changing that belt's speed in the inspector
    /// has to reach it. Which belt that is, is the scene's business and not the
    /// belt's.</summary>
    void IPartHost.NoteTransportSpeed(string instanceId, float speed)
    {
        if (Scene is not null && instanceId == SortingTags.ConveyorId) Scene.TransportSpeed = speed;
    }

    /// <summary>
    /// Free any carton that has fallen off the world, and update the live
    /// count the toolbar's status chip reads. Boxes are children of the
    /// scene root, not <see cref="_placedParts"/> — the same place
    /// <see cref="ResetItems"/> already looks for them.
    /// </summary>
    private void SweepBoxes()
    {
        int count = 0;
        int killed = 0;
        foreach (var child in GetParent()?.GetChildren() ?? new Godot.Collections.Array<Node>())
        {
            if (child is not BoxPhysics box) continue;
            if (box.GlobalPosition.Y < KillPlaneY)
            {
                box.QueueFree();
                killed++;
                continue;
            }
            count++;
        }

        if (killed > 0)
            GD.Print($"kill plane: freed {killed} carton(s) that fell off the world");

        LiveItemCount = count;
        bool overCap = count >= LiveItemCap;
        if (overCap && !ItemCapHit)
        {
            ItemCapHit = true;
            GD.PushWarning($"item cap reached ({LiveItemCap} live cartons) — the emitter is "
                          + "paused until the count drops");
        }
        else if (!overCap && ItemCapHit)
        {
            ItemCapHit = false;
        }
    }
}
