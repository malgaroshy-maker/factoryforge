using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// The editor's keyboard, driven as a keyboard (HP-38, HP-39).
///
/// <code>godot --headless --path engine -- --self-test=editorkeys</code>
///
/// Both bugs this covers were invisible to every existing test for the same
/// reason: the tests call the editor's API, and the API was never wrong. The
/// keystroke was, and so was the camera the keystroke is resolved against.
///
/// <b>HP-38 — one key, one action.</b> Events go through
/// <c>Viewport.PushInput</c>, not through a direct method call, so
/// <c>Viewport.IsInputHandled()</c> answers the question that actually mattered:
/// does this keystroke stop here, or does it carry on to <c>Main</c> and do a
/// second thing? Ctrl+C copied without claiming and toggled the camera as well;
/// Ctrl+R rotated the selection on its way to resetting the simulation.
///
/// <b>HP-39 — the nudge must follow the screen.</b> This is the half no headless
/// test could see, and it is worth being precise about why:
/// <c>NudgeSelectedPart</c> reads the current camera, and with no camera it
/// leaves the heading at zero and takes the one path that was always correct.
/// <c>--self-test=buildflow</c> runs headless with no camera and so exercised
/// exactly the branch without the bug. This test builds a real
/// <c>Camera3D</c> and puts it at each of the standard viewing angles, using
/// <see cref="View.OrbitCamera"/>'s own yaw/pitch arithmetic rather than a
/// hand-made transform, so it is testing the views the 1-4 keys actually
/// produce.
/// </summary>
public partial class EditorKeysSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

    private readonly List<string> _failures = new();
    private Camera3D _camera = null!;
    private bool _done;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    public override void _PhysicsProcess(double delta)
    {
        // Latched, because Quit() only takes effect at the end of the frame and
        // a second pass would run against state the first already mutated.
        if (_done) return;
        _done = true;

        try
        {
            _camera = new Camera3D { Name = "SelfTestCamera" };
            AddChild(_camera);
            _camera.MakeCurrent();

            Editor.SetMode(EditorMode.Edit);
            Editor.ClearAllPlacedParts();

            CheckNudgeFollowsTheScreen();
            CheckOneKeyOneAction();
        }
        catch (System.Exception ex)
        {
            // An exception inside _PhysicsProcess is logged by Godot and the run
            // continues to its --duration, so a throw here would otherwise end
            // as exit 0 with no report at all (gotcha 12).
            Expect(false, $"threw: {ex}");
        }

        Finish();
    }

    private void Finish()
    {
        if (_failures.Count == 0)
        {
            GD.Print("self-test editorkeys: PASS");
            GetTree().Quit(0);
            return;
        }
        GD.PrintErr($"self-test editorkeys: FAIL ({_failures.Count})");
        GetTree().Quit(1);
    }

    // ---------- HP-39

    /// <summary>Stand the camera where <see cref="View.OrbitCamera"/> would for
    /// a given yaw and pitch, looking at the origin. The same arithmetic as
    /// <c>OrbitCamera.Apply</c>, so the angles below are the ones the 1-4 keys
    /// really give.</summary>
    private void PointCamera(float yaw, float pitch)
    {
        var target = Vector3.Zero;
        var offset = new Vector3(
            Mathf.Cos(pitch) * Mathf.Sin(yaw),
            -Mathf.Sin(pitch),
            Mathf.Cos(pitch) * Mathf.Cos(yaw)) * 3.0f;
        _camera.Position = target + offset;
        _camera.LookAt(target, Vector3.Up);
    }

    private void CheckNudgeFollowsTheScreen()
    {
        // The five standard angles, and what "press Right" has to mean in each.
        // Screen right is the camera's own +X basis vector, snapped to the
        // nearest world axis -- which is the whole contract, stated in world
        // terms so the assertion is readable.
        var views = new (string Name, float Yaw, float Pitch, Vector3 Right)[]
        {
            ("front (key 3)", 0.0f, -0.18f, Vector3.Right),
            ("side (key 4)", -Mathf.Pi / 2.0f, -0.18f, Vector3.Back),
            ("side, from the other hand", Mathf.Pi / 2.0f, -0.18f, Vector3.Forward),
            ("from behind", Mathf.Pi, -0.18f, Vector3.Left),
            ("iso (the default)", -0.62f, -0.55f, Vector3.Right),
            ("top (key 2)", -0.62f, -1.45f, Vector3.Right),
        };

        foreach (var view in views)
        {
            PointCamera(view.Yaw, view.Pitch);

            // Sanity: the world direction this test expects is the camera's own
            // screen-right, snapped. Without this the test would only be
            // asserting that the editor agrees with a table somebody typed --
            // and a wrong table would pass forever.
            Vector3 screenRight = SnapToAxis(_camera.GlobalBasis.X);
            Expect(screenRight.IsEqualApprox(view.Right),
                   $"{view.Name}: the camera's own screen-right is {Describe(view.Right)} "
                   + $"(measured {Describe(screenRight)})");

            Vector3 moved = NudgeAndMeasure(new Vector2(1, 0));
            Expect(moved.Normalized().IsEqualApprox(view.Right),
                   $"{view.Name}: Right moves the part {Describe(view.Right)}, "
                   + $"not {Describe(moved.Normalized())}");

            Vector3 away = NudgeAndMeasure(new Vector2(0, 1));
            Vector3 expectedAway = SnapToAxis(-_camera.GlobalBasis.Z);
            Expect(away.Normalized().IsEqualApprox(expectedAway),
                   $"{view.Name}: Up moves the part away from the camera "
                   + $"({Describe(expectedAway)}), not {Describe(away.Normalized())}");
        }
    }

    /// <summary>Place a belt, press an arrow key, and report which way it went.
    /// Through the real key event, not <c>NudgeSelectedPart</c> directly, so the
    /// binding is covered as well as the arithmetic.</summary>
    private Vector3 NudgeAndMeasure(Vector2 screenDelta)
    {
        Editor.ClearAllPlacedParts();
        Editor.SetPlacementPart("ConveyorBelt");
        Editor.PlacePreviewAt(Vector3.Zero);
        Editor.CancelPlacement();
        Editor.SelectPartByIndex(0);

        if (Editor.SelectedPosition is not { } before)
        {
            Expect(false, "the belt this test nudges could not be selected");
            return Vector3.Zero;
        }

        Press(ArrowFor(screenDelta));
        return (Editor.SelectedPosition ?? before) - before;
    }

    private static Key ArrowFor(Vector2 screenDelta) =>
        screenDelta.X > 0 ? Key.Right
        : screenDelta.X < 0 ? Key.Left
        : screenDelta.Y > 0 ? Key.Up : Key.Down;

    /// <summary>The nearest world axis, so "roughly right" reads as Right. The
    /// editor snaps the camera heading to a quarter turn for the same reason:
    /// a nudge has to land on the grid at every viewing angle.</summary>
    private static Vector3 SnapToAxis(Vector3 v)
    {
        var flat = new Vector3(v.X, 0, v.Z);
        if (flat.IsZeroApprox()) return Vector3.Zero;
        return Mathf.Abs(flat.X) >= Mathf.Abs(flat.Z)
            ? new Vector3(Mathf.Sign(flat.X), 0, 0)
            : new Vector3(0, 0, Mathf.Sign(flat.Z));
    }

    private static string Describe(Vector3 v)
    {
        if (v.IsEqualApprox(Vector3.Right)) return "+X (right)";
        if (v.IsEqualApprox(Vector3.Left)) return "-X (left)";
        if (v.IsEqualApprox(Vector3.Back)) return "+Z (towards the viewer)";
        if (v.IsEqualApprox(Vector3.Forward)) return "-Z (away from the viewer)";
        return v.ToString();
    }

    // ---------- HP-38

    private void CheckOneKeyOneAction()
    {
        Editor.ClearAllPlacedParts();
        Editor.SetPlacementPart("ConveyorBelt");
        Editor.PlacePreviewAt(Vector3.Zero);
        Editor.CancelPlacement();
        Editor.SelectPartByIndex(0);
        Expect(Editor.SelectedInstanceId is not null, "a belt is selected to press keys at");

        float TurnOf() => Editor.SelectedRotationY ?? float.NaN;

        // Ctrl+R is Main's "reset the simulation". The editor must not also
        // rotate, and must not swallow the key -- Main never sees a claimed one.
        float before = TurnOf();
        bool claimed = Press(Key.R, ctrl: true);
        Expect(Mathf.IsEqualApprox(TurnOf(), before),
               $"Ctrl+R does not rotate the selection (turned {TurnOf() - before} rad)");
        Expect(!claimed, "and does not claim the key, so the simulation still resets");

        // Plain R rotates, and stops there.
        claimed = Press(Key.R);
        Expect(Mathf.IsEqualApprox(Mathf.Wrap(TurnOf() - before, -Mathf.Pi, Mathf.Pi),
                                   Mathf.Pi / 2.0f),
               $"R alone turns the selection a quarter turn (turned {TurnOf() - before} rad)");
        Expect(claimed, "and claims the key");

        // Ctrl+C is Main's "toggle the camera" if it gets through. It must not.
        int copiedBefore = Editor.ClipboardCount;
        claimed = Press(Key.C, ctrl: true);
        Expect(Editor.ClipboardCount > copiedBefore || Editor.ClipboardCount == 1,
               $"Ctrl+C copies the selection (clipboard holds {Editor.ClipboardCount})");
        Expect(claimed, "and claims the key, so the camera does not flip as well");

        // A key the editor does not want has to carry on. Space is the
        // simulation's pause and the editor has no business eating it.
        Expect(!Press(Key.Space), "a key the editor does not handle is left for Main");

        // Escape is shared on purpose: it cancels a placement here and dismisses
        // an overlay there, and both should happen.
        Editor.SetPlacementPart("ConveyorBelt");
        Expect(!Press(Key.Escape), "Escape is deliberately shared with Main's overlays");
        Expect(!Editor.HasPlacementPreview, "and still cancels the placement here");
    }

    /// <summary>Send a key the way the window does, and report whether anything
    /// claimed it. <c>PushInput</c> rather than a direct call to
    /// <c>_UnhandledInput</c>, because "did this stop here?" is the entire
    /// question HP-38 turns on and only the viewport can answer it.</summary>
    private bool Press(Key keycode, bool ctrl = false)
    {
        var ev = new InputEventKey
        {
            Keycode = keycode,
            PhysicalKeycode = keycode,
            Pressed = true,
            CtrlPressed = ctrl,
        };
        var viewport = GetViewport();
        viewport.PushInput(ev);
        return viewport.IsInputHandled();
    }
}
