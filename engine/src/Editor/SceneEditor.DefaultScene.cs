using FactoryForge.Parts;
using FactoryForge.Scenes;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// The sorting line the engine opens on, built out of real parts.
///
/// A <em>scene</em>, kept apart from the editor proper (HP-34/HP-36) rather
/// than buried in it. The editor is not allowed to know a part type's name any
/// more — that is the whole point of HP-34, and the check that keeps it true
/// scans <c>engine/src/Editor/</c> for exactly that. A scene is the one thing
/// that must name the parts it places, in the same way
/// <c>engine/templates/*.json</c> and <see cref="Scenes.SortingScene"/> do, so
/// it lives in a file of its own where the exemption is visible instead of
/// being an exception buried in four thousand lines that also do the
/// dispatching.
/// </summary>
public partial class SceneEditor
{
    /// <summary>
    /// Build the sorting line out of real parts.
    /// </summary>
    /// <param name="physical">
    /// When true the parts are authoritative: sensors raycast against real
    /// cartons, the pusher reports its own limit switches, an emitter spawns
    /// rigid bodies and removers count them. When false they are views of a
    /// <see cref="SortingScene"/> that owns the same tags and simulates the
    /// boxes itself. The layout and the tag interface are identical either way,
    /// which is the point: the same PLC program drives both.
    /// </param>
    public void RegisterDefaultSceneParts(bool physical = false)
    {
        ClearAllPlacedParts();

        // The instance id is the tag *prefix*, never a whole tag name: the part
        // dispatch in _Process appends the suffix ("conveyor" -> conveyor.rotate).
        // Registering "conveyor.rotate" here silently disables the part, because
        // "conveyor.rotate.rotate" matches nothing.
        // Every part sits on a grid point at the work plane (see PartLayout):
        // X and Z are multiples of the 0.5 m cell, Y is always WorkPlaneY. The
        // scene constants are already on the grid, so the layout falls out of
        // them — no hand-tuned offsets, and "Save Scene" round-trips cleanly.
        const float y = PartLayout.WorkPlaneY;
        const float lane = (float)SortingScene.ChuteLane;

        double length = SortingScene.RemoverPos - SortingScene.EmitterPos;
        var beltNode = new ConveyorBelt
        {
            Position = new Vector3((float)(length / 2), y, 0),
            Size = new Vector3((float)length, PartLayout.BeltThickness, 0.5f),
            Speed = (float)SortingScene.BeltSpeed,
        };
        GetParent()?.AddChild(beltNode);
        Adopt(beltNode, SortingTags.ConveyorId, "ConveyorBelt");

        // Mounting heights are what makes the scene sort: the low beam sees every
        // box, the high beam only clears the tall ones. Range reaches from the
        // post across to the far belt edge.
        var sensorLowNode = new PhotoelectricSensor
        {
            Position = new Vector3((float)SortingScene.SensorLowPos, y, lane),
            Range = 0.75f,
            HeightAboveBelt = 0.04f,
            VisualOnly = !physical,
        };
        GetParent()?.AddChild(sensorLowNode);
        Adopt(sensorLowNode, SortingTags.SensorLowId, "PhotoelectricSensor");

        var sensorHighNode = new PhotoelectricSensor
        {
            Position = new Vector3((float)SortingScene.SensorHighPos, y, lane),
            Range = 0.75f,
            HeightAboveBelt = 0.20f,
            VisualOnly = !physical,
        };
        GetParent()?.AddChild(sensorHighNode);
        Adopt(sensorHighNode, SortingTags.SensorHighId, "PhotoelectricSensor");

        // Beside the belt, not on it: the pusher's origin is its mounting point
        // and it strokes towards +Z, across the lane and onto the chute.
        const float pusherStroke = 0.55f;
        var pusherNode = new PusherMechanism
        {
            Position = new Vector3((float)SortingScene.PusherPos, y, -lane),
            StrokeLength = pusherStroke,
            // Match the simulated stroke, so the plate hits its limit exactly
            // when the scene reports pusher.extended.
            ExtendSpeed = pusherStroke / (float)SortingScene.PusherTravelTime,
            VisualOnly = !physical,
        };
        GetParent()?.AddChild(pusherNode);
        Adopt(pusherNode, SortingTags.PusherId, "PusherMechanism");

        var chuteNode = new Chute
        {
            Position = new Vector3((float)SortingScene.PusherPos, y, lane),
        };
        GetParent()?.AddChild(chuteNode);
        Adopt(chuteNode, "chute_1", "Chute");

        var lightNode = new StackLight
        {
            Position = new Vector3(-lane, y, lane),
        };
        GetParent()?.AddChild(lightNode);
        Adopt(lightNode, SortingTags.StackLightId, "StackLight");

        // An operator station at the head of the line. Unlike the parts above it
        // is not a view of tags SortingScene owns — nothing in the deterministic
        // scene presses buttons — so it registers its own, in both modes. Without
        // it in the default scene, Run mode would open onto a line with nothing
        // to click and look broken.
        //
        // On the near side of the line — the same side the default camera looks
        // from — because a button you cannot see is a button you cannot press.
        // The caps already face +Z, which is where an operator stands looking at
        // the machine, so it needs no rotation. Kept at the head of the line so
        // it neither hides the sorting zone (sensors at 1.5 and 2.0, pusher at
        // 2.5) nor sits under the parts palette down the left of the screen.
        var panelNode = new ButtonPanel
        {
            Position = new Vector3(lane, y, 2.0f * lane),
        };
        // This line sorts on two beams, so its one analog knob is the timing
        // pot every real diverter has: how long after the tall beam breaks
        // the pusher fires. Turn it wrong and cartons are struck on the nose
        // or missed entirely, which is exactly what the pot is for (OP-03).
        panelNode.ConfigureSetpoint(0.30f, 1.80f, "s", 0.90f);
        GetParent()?.AddChild(panelNode);
        if (Tags is not null)
        {
            var (panelId, panelOwns) = PartTagManager.RegisterPartTags(panelNode, "ButtonPanel", Tags, "panel");
            _placedParts.Add(new PlacedPart(panelNode, panelId, "ButtonPanel", panelOwns,
                                            NextPartKey()));
        }

        // Only the rigid-body scene needs these: the deterministic scene creates
        // and retires its own boxes in code.
        if (physical)
        {
            var emitterNode = new Emitter
            {
                Position = new Vector3((float)SortingScene.EmitterPos, y, 0),
            };
            GetParent()?.AddChild(emitterNode);
            Adopt(emitterNode, SortingTags.EmitterId, "Emitter");

            var shortRemover = new Remover
            {
                Position = new Vector3((float)SortingScene.RemoverPos + 0.25f, y - 0.2f, 0),
                ZoneSize = new Vector3(0.5f, 0.6f, 0.6f),
                CountTag = SortingTags.CounterShort,
            };
            GetParent()?.AddChild(shortRemover);
            Adopt(shortRemover, "remover_short", "Remover");

            // Under the chute's discharge, so a diverted carton is counted once
            // it has actually made it down the ramp.
            var tallRemover = new Remover
            {
                Position = new Vector3((float)SortingScene.PusherPos, 0.15f, lane + 0.5f),
                ZoneSize = new Vector3(0.6f, 0.4f, 0.6f),
                CountTag = SortingTags.CounterTall,
            };
            GetParent()?.AddChild(tallRemover);
            Adopt(tallRemover, "remover_tall", "Remover");
        }

        NotifyTagsChanged();
        CallDeferred(nameof(AnnounceSceneLoaded));

        void Adopt(Node3D node, string instanceId, string partType) =>
            _placedParts.Add(new PlacedPart(node, instanceId, partType, OwnsTags: false,
                                            NextPartKey()));
    }
}
