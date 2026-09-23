using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// End-to-end check of Run mode: synthesize a real mouse click at a button's
/// screen position and assert the tag moved. Run it with:
///
/// <code>godot --path engine -- --self-test=click</code>
///
/// Needs a display, so it cannot run in headless CI — <c>--self-test=buttons</c>
/// is the headless half, covering the same panel from the tag side.
///
/// This half exists because the two halves fail differently, and only this one
/// catches the failure that actually happened: the press dispatch was correct,
/// the hit test was correct, and clicking still did nothing, because the click
/// handler asked the viewport where the cursor was instead of asking the event
/// where the click was. Every assertion below passed at the level the headless
/// test can see while the feature was, from a user's seat, completely dead.
/// </summary>
public partial class ClickPathSelfTest : Node
{
    public TagTable Tags { get; set; } = null!;
    public SceneEditor? Editor { get; set; }

    private ButtonPanel _panel = null!;
    private Camera3D _camera = null!;
    private int _step;
    private int _highTicks;
    private int _edges;
    private bool _prevStart;
    private bool _capMatched = true;
    private bool _done;
    private readonly List<string> _failures = new();

    /// <summary>IP-31's floor, the same number <c>--self-test=buttons</c>
    /// holds the panel to: a click must stay high long enough for a 100 ms
    /// poll to see it twice, not one physics tick.</summary>
    private const float MinVisibleHold = 0.2f;

    // The click on Start and the watch that follows it, in physics steps. The
    // watch runs well past the hold so a press that never let go is caught.
    private const int ClickStep = 30;
    private int _watchEnd;

    public override void _Ready()
    {
        foreach (var child in GetParent().GetChildren())
        {
            if (child is ButtonPanel found) { _panel = found; break; }
        }

        if (_panel is null || Editor is null)
        {
            GD.PrintErr("self-test click: no ButtonPanel or editor in the scene");
            GetTree().Quit(1);
        }
    }

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    private bool Tag(string suffix) =>
        Tags.Contains($"panel.{suffix}") && (bool)Tags.Visible($"panel.{suffix}");

    /// <summary>Click where a point on the panel appears on screen, the way a
    /// user would: an event carrying a screen position, pushed through the same
    /// input queue the window feeds.
    ///
    /// <see cref="Camera3D.UnprojectPosition"/> answers in the viewport's own
    /// coordinate space (project.godot's viewport_width/height). A real OS
    /// click arrives in window pixels and Godot's <c>canvas_items</c> stretch
    /// mode (see FF-32) rescales it to viewport space before any script ever
    /// sees it — so an event built directly from UnprojectPosition and handed
    /// to <see cref="Input.ParseInputEvent"/> gets that same rescale applied a
    /// second time, landing off by the window/viewport ratio. Small enough
    /// that it stayed inside a cap's hit margin by luck until FF-25 changed
    /// the panel's proportions and pushed it out. Converting back to window
    /// space here is what a real OS event would already be in.</summary>
    private void ClickAt(Vector3 localPoint)
    {
        Vector2 viewportPos = _camera.UnprojectPosition(_panel.GlobalTransform * localPoint);
        Vector2 windowSize = DisplayServer.WindowGetSize();
        Vector2 viewportSize = _camera.GetViewport().GetVisibleRect().Size;
        Vector2 windowPos = viewportPos * (windowSize / viewportSize);

        Input.ParseInputEvent(new InputEventMouseButton
        {
            ButtonIndex = MouseButton.Left,
            Pressed = true,
            Position = windowPos,
            GlobalPosition = windowPos,
        });
    }

    public override void _PhysicsProcess(double delta)
    {
        if (_done) return;
        _step++;
        try
        {
            Step();
        }
        catch (System.Exception ex)
        {
            // Gotcha 12: logged and swallowed otherwise, and the run would end
            // looking like a pass.
            Expect(false, $"threw at step {_step}: {ex}");
            Report();
        }
    }

    private void Step()
    {
        // Give the camera a few frames to settle before projecting through it.
        if (_step < 20) return;
        _camera = GetViewport().GetCamera3D();
        if (_camera is null)
        {
            GD.Print("self-test click: SKIPPED (needs a display; use --self-test=buttons headless)");
            _done = true;
            GetTree().Quit(0);
            return;
        }

        switch (_step)
        {
            case 20:
                // A click in Edit mode selects and must not press anything.
                // These local-space offsets mirror ButtonPanel's own layout
                // (see PartLayout.PanelWidth/PanelHeight, FF-25) — the same
                // literals PanelSelfTest's headless HitTest check uses.
                Editor!.SetMode(EditorMode.Edit);
                ClickAt(new Vector3(-0.12f, 0.3132f, 0.06f));
                break;

            case 24:
                Expect(!Tag("start"), "a click in Edit mode does not press Start");
                Editor!.SetMode(EditorMode.Run);
                break;

            case ClickStep:
                ClickAt(new Vector3(-0.12f, 0.3132f, 0.06f));
                _highTicks = 0;
                _edges = 0;
                _prevStart = false;
                _watchEnd = ClickStep + 3 * _panel.PressHoldTicks + 12;
                break;

            case > ClickStep when _step <= _watchEnd:
            {
                bool start = Tag("start");
                if (start) _highTicks++;
                if (start && !_prevStart) _edges++;
                _prevStart = start;
                // What the cap shows is what the controller gets.
                if (start != _panel.IsHeld(PanelButton.Start)) _capMatched = false;

                if (_step == _watchEnd) FinishStartClick();
                break;
            }

            case var s when s == _watchEnd + 8:
                if (Tag("start") || Tag("stop") || Tag("reset")) _highTicks++;
                Expect(_highTicks == 0, "clicking the housing presses no button at all");
                Expect(Tag("estop"), "estop still healthy before the mushroom is struck");
                ClickAt(new Vector3(0.0f, 0.1398f, 0.06f));
                break;

            case var s when s > _watchEnd && s < _watchEnd + 8:
                if (Tag("start") || Tag("stop") || Tag("reset")) _highTicks++;
                break;

            case var s when s == _watchEnd + 12:
                Expect(!Tag("estop"), "clicking the mushroom breaks the estop circuit");
                ClickAt(new Vector3(0.0f, 0.1398f, 0.06f));
                break;

            case var s when s == _watchEnd + 16:
                Expect(Tag("estop"), "clicking the mushroom again releases it");
                Report();
                break;
        }
    }

    /// <summary>One click on Start, seen from the tag: one edge, held for the
    /// panel's hold -- at least <see cref="MinVisibleHold"/> -- and then gone.
    /// Then the next click, on bare housing.</summary>
    private void FinishStartClick()
    {
        int minTicks = Mathf.CeilToInt(MinVisibleHold * Engine.PhysicsTicksPerSecond - 1e-3f);
        Expect(_edges == 1, $"a click on the start cap makes exactly one edge (saw {_edges})");
        Expect(_highTicks >= minTicks,
               $"and holds it at least {MinVisibleHold} s = {minTicks} ticks, not one scan (saw {_highTicks})");
        Expect(_highTicks == _panel.PressHoldTicks,
               $"and for exactly the panel's hold, {_panel.PressHoldTicks} ticks (saw {_highTicks})");
        Expect(!Tag("start"), "start has let go again well after the hold");
        Expect(_capMatched, "the cap is shown down exactly while start is high");
        Expect(!Tag("stop") && !Tag("reset"), "clicking Start presses nothing else");

        // Bare housing, well away from any cap.
        ClickAt(new Vector3(0.0f, 0.50f, 0.06f));
        _highTicks = 0;
    }

    private void Report()
    {
        if (_done) return;
        _done = true;
        if (_failures.Count == 0)
        {
            GD.Print("self-test click: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test click: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }
}
