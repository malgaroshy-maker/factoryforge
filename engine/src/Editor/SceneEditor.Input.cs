using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
    /// <summary>
    /// Take this event out of everyone else's hands (ES-05).
    ///
    /// The orbit camera and the editor both listen on `_UnhandledInput`, and
    /// neither used to claim anything — so **dragging a part also orbited the
    /// view**, because both handlers saw the same motion and both acted on it.
    /// The part went where the cursor went and the world turned underneath it
    /// at the same time, which reads as the drag being broken rather than as
    /// two features fighting.
    ///
    /// Claimed only for the gestures the editor is actually using — a part
    /// drag and a selection box — so a plain left-drag on empty floor still
    /// orbits, which is the most-used gesture in the app and must not become
    /// collateral damage.
    /// </summary>
    private void ClaimInput() => GetViewport()?.SetInputAsHandled();

    public override void _UnhandledInput(InputEvent @event)
    {
        // F1 is the one binding that works in both modes; everything else below
        // is editing, and editing is exactly what Run mode switches off.
        if (@event is InputEventKey modeKey && modeKey.Pressed && !modeKey.Echo
            && modeKey.Keycode == Key.F1)
        {
            ToggleMode();
            return;
        }

        // Save and Open work in both modes, checked ahead of the Run-mode
        // return below. Undo/redo/move/rotate/delete are genuinely edit-only;
        // Ctrl+S silently doing nothing in Run mode was never a deliberate
        // choice, just a side effect of that same early return (§2.7 -> UX-40).
        if (@event is InputEventKey saveOpenKey && saveOpenKey.Pressed && !saveOpenKey.Echo
            && saveOpenKey.CtrlPressed)
        {
            if (saveOpenKey.Keycode == Key.S) { Toolbar?.ShowSaveDialog(); return; }
            if (saveOpenKey.Keycode == Key.O) { Toolbar?.ShowLoadDialog(); return; }
        }

        if (Mode == EditorMode.Run)
        {
            if (@event is InputEventMouseButton runClick && runClick.ButtonIndex == MouseButton.Left)
            {
                // The event's own position, not the live cursor: they agree for a
                // real click but only the event knows where the click happened,
                // which is the difference between a testable path and one that
                // can only be checked by hand.
                if (!runClick.Pressed) EndDialDrag();
                else if (FaultToolArmed) FaultAt(runClick.Position);
                else if (!BeginDialDragAt(runClick.Position)) PressControlAt(runClick.Position);
                ClaimInput();
            }
            else if (@event is InputEventKey runKey && runKey.Pressed && !runKey.Echo
                     && runKey.Keycode == Key.Escape)
            {
                SetFaultToolArmed(false);
            }
            else if (@event is InputEventMouseMotion runMotion)
            {
                // A pot is turned, not pressed, so the drag owns the mouse
                // until the button comes back up -- including the hover
                // highlight, which would otherwise chase whatever the cursor
                // wandered over mid-turn.
                // The pot is turned by dragging, and without claiming the
                // motion the camera orbits at the same time -- the same
                // collision ES-05 fixes in Edit mode, on the one Run-mode
                // gesture that is a drag rather than a click.
                if (IsDraggingDial) { DragDial(-runMotion.Relative.Y); ClaimInput(); }
                else UpdateHoverHighlight(runMotion.Position);
            }
            return;
        }

        if (_previewNode is not null && @event is InputEventMouseMotion)
        {
            UpdatePreviewPosition();
        }
        else if (_previewNode is not null && @event is InputEventMouseButton mouseBtn && mouseBtn.Pressed)
        {
            if (mouseBtn.ButtonIndex == MouseButton.Left)
            {
                PlaceCurrentPart();
            }
            else if (mouseBtn.ButtonIndex == MouseButton.Right)
            {
                ClearPreview();
            }
        }
        else if (_previewNode is null && @event is InputEventMouseButton clickBtn
                 && clickBtn.ButtonIndex == MouseButton.Left)
        {
            // Press selects and *arms* a move; release commits it. Everyone
            // tries dragging a part first, and until this landed the only way
            // to move one was the M key, which nothing on screen mentioned
            // (OP-08). A press that never travels is still a plain click, so
            // selecting did not have to change to make dragging work.
            if (clickBtn.Pressed)
            {
                // The event's own position, not the live cursor — the same
                // correction Run mode's dispatch already carries. They agree
                // for a real click, but a drag has to grab the part under the
                // *press*, and only the event knows where that was.
                if (PickPartAt(clickBtn.Position) is { } hit)
                {
                    if (clickBtn.ShiftPressed) ToggleSelection(hit);
                    // Pressing on something already selected keeps the whole
                    // group, so a group can be dragged by any member of it.
                    // Pressing on anything else selects just that one.
                    else if (!_selection.Contains(hit)) SelectOnly(hit);
                    ArmPartDrag(clickBtn.Position);
                    ClaimInput();
                }
                else if (clickBtn.CtrlPressed)
                {
                    // Ctrl, because a plain left-drag on empty floor is how the
                    // camera orbits and that is the most-used gesture in the
                    // app. Empty space without Ctrl still deselects on the
                    // press, exactly as it did before (ES-02).
                    BeginSelectionRect(clickBtn.Position, clickBtn.ShiftPressed);
                    ClaimInput();
                }
                else if (!clickBtn.ShiftPressed)
                {
                    DeselectPart();
                }
            }
            else if (_selectionRectActive)
            {
                EndSelectionRect(clickBtn.Position);
                ClaimInput();
            }
            else if (_partDrag is not null)
            {
                EndPartDrag();
                ClaimInput();
            }
        }
        else if (_previewNode is null && _selectionRectActive
                 && @event is InputEventMouseMotion rectMotion)
        {
            UpdateSelectionRect(rectMotion.Position);
            ClaimInput();
        }
        else if (_previewNode is null && _partDrag is not null
                 && @event is InputEventMouseMotion dragMotion)
        {
            UpdatePartDrag(dragMotion.Position);
            ClaimInput();
        }
        else if (@event is InputEventKey keyEvent && keyEvent.Pressed && !keyEvent.Echo)
        {
            HandleEditorKey(keyEvent);
        }
    }

    /// <summary>
    /// Edit mode's keyboard, and the one rule it has: a key this consumes must
    /// be claimed, and a key it does not want must be left alone (HP-38).
    ///
    /// Both halves were broken, in opposite directions and on adjacent lines.
    /// Ctrl+C copied and did not claim, so it fell through to <c>Main</c>'s
    /// bare <c>Key.C</c> and toggled the camera as well — one keystroke, two
    /// actions, one of which the user did not ask for and cannot undo. Ctrl+R
    /// rotated the selection because the rotate branch did not exclude Ctrl,
    /// while <c>Main</c> treats Ctrl+R as "reset the simulation" — so the reset
    /// also turned whatever was selected. Two lines below, <c>Key.N</c> was
    /// already guarded with <c>!keyEvent.CtrlPressed</c>: the convention existed
    /// and had been applied to one key out of three.
    ///
    /// Escape is deliberately not claimed. It cancels a placement here *and*
    /// dismisses an overlay in <c>Main</c>, and those are two different things
    /// that happen to share a key by long convention.
    /// </summary>
    private void HandleEditorKey(InputEventKey keyEvent)
    {
        bool handled = true;

        if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.Z)
        {
            Undo();
        }
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.Y)
        {
            Redo();
        }
            // Ctrl+S / Ctrl+O are handled above, ahead of the Run-mode return,
            // so they are unreachable here (Edit mode already returned via
            // that branch too) rather than duplicated.
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.D && _selectedPart is not null)
        {
            DuplicateSelectedPart();
        }
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.A)
        {
            SelectEverything();
        }
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.C)
        {
            CopySelection();
        }
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.V)
        {
            PasteClipboard();
        }
        else if (keyEvent.Keycode == Key.M && _selectedPart is not null)
        {
            StartMoveSelectedPart();
        }
        else if (keyEvent.Keycode == Key.R && !keyEvent.CtrlPressed && _previewNode is not null)
        {
            _previewRotationY += Mathf.Pi / 2.0f;
            _previewNode.Rotation = new Vector3(0, _previewRotationY, 0);
        }
        else if (keyEvent.Keycode == Key.R && !keyEvent.CtrlPressed
                 && _previewNode is null && _selectedPart is not null)
        {
            RotateSelectedPart();
        }
        else if (keyEvent.Keycode == Key.N && !keyEvent.CtrlPressed)
        {
            TogglePartNames();
        }
        else if (keyEvent.Keycode == Key.Escape)
        {
            ClearPreview();
            DeselectPart();
            // Shared with Main's overlay dismissal on purpose. See the summary.
            handled = false;
        }
        else if (_selectedPart is not null && !keyEvent.CtrlPressed
                 && NudgeFor(keyEvent.Keycode) is { } nudge)
        {
            NudgeSelectedPart(nudge);
        }
        else if (keyEvent.Keycode == Key.Delete || keyEvent.Keycode == Key.Backspace)
        {
            DeleteSelectedPart();
        }
        else
        {
            handled = false;
        }

        if (handled) ClaimInput();
    }

    /// <summary>Screen-space direction for an arrow key, or null for anything
    /// else. Kept as one table rather than four branches so the key list and
    /// the handler cannot disagree about which keys nudge.</summary>
    private static Vector2? NudgeFor(Key keycode) => keycode switch
    {
        Key.Left => new Vector2(-1, 0),
        Key.Right => new Vector2(1, 0),
        Key.Up => new Vector2(0, 1),
        Key.Down => new Vector2(0, -1),
        _ => null,
    };
}
