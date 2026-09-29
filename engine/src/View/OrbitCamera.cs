using Godot;

namespace FactoryForge.View;

/// <summary>
/// Orbit camera: drag to rotate, wheel to zoom, middle-drag to pan, F to frame
/// whatever is selected.
///
/// The motion is damped (CP-16): input moves a *desired* pose and the camera
/// chases it. Applying input straight to the transform, which is what this used
/// to do, makes every wheel click a jump-cut and every drag a hard stop — the
/// single cheapest thing that separates a viewport that feels like a tool from
/// one that feels like a debug view. Damping is frame-rate independent, so a
/// 30 fps machine and a 144 fps one settle over the same wall-clock time.
/// </summary>
public partial class OrbitCamera : Camera3D
{
    [Export] public Vector3 Target { get; set; } = new(1.6f, 0.68f, 0.05f);
    [Export] public float Distance { get; set; } = 2.9f;
    [Export] public float Yaw { get; set; } = -0.62f;
    [Export] public float Pitch { get; set; } = -0.30f;

    private const float MinPitch = -1.45f;
    private const float MaxPitch = -0.05f;

    /// <summary>Fraction of the remaining error left after one second. 0.0005
    /// settles in roughly a fifth of a second — quick enough that dragging
    /// feels direct, slow enough that a wheel click reads as a move.</summary>
    private const float SettleFactor = 0.0005f;

    /// <summary>Below this the chase stops and the pose snaps, so the camera
    /// does not spend every frame of an idle scene applying a transform that
    /// differs in the seventh decimal place.</summary>
    private const float SettleEpsilon = 0.0005f;

    private Vector3 _targetGoal;
    private float _distanceGoal;
    private float _yawGoal;
    private float _pitchGoal;
    private bool _settled;

    public override void _Ready()
    {
        _targetGoal = Target;
        _distanceGoal = Distance;
        _yawGoal = Yaw;
        _pitchGoal = Pitch;
        Apply();
    }

    /// <summary>
    /// Frame a box: point at its centre and back off far enough to see all of
    /// it. Used by F on the current selection (CP-16), which was previously a
    /// matter of scrolling and dragging until the part you had just placed came
    /// back into shot.
    /// </summary>
    /// <param name="overviewPitch">Tip the camera down to a standard
    /// three-quarter view as well as moving it. Used when a whole scene is
    /// opened, where the current angle carries no intent worth preserving —
    /// and deliberately not used by F on a selection, where it does: somebody
    /// who has orbited round to look at the back of a pusher wants to keep
    /// looking at the back of it.</param>
    public void Frame(Aabb bounds, bool overviewPitch = false)
    {
        if (_pinned) return;    // placed by --camera=, see Place
        _targetGoal = bounds.GetCenter();
        if (overviewPitch) _pitchGoal = Mathf.Clamp(-0.52f, MinPitch, MaxPitch);

        // Fit the sphere that contains the box into the vertical field of view,
        // with a margin so the part is not touching the frame edge. Using the
        // whole diagonal rather than the tallest side means a long belt is
        // framed by its length, which is what you actually want to see.
        float radius = Mathf.Max(bounds.Size.Length() * 0.5f, 0.2f);
        float halfFov = Mathf.DegToRad(Fov * 0.5f);
        // 1.15, not the 1.6 this started at: the bounding *sphere* already
        // over-covers a box-shaped line by a long way, so a generous margin on
        // top of it framed a whole template at about a quarter of the screen.
        _distanceGoal = Mathf.Clamp(radius / Mathf.Tan(halfFov) * 1.15f, 1.0f, 20.0f);
        _settled = false;
    }

    /// <summary>
    /// Swing to a standard viewing angle, keeping whatever is framed (NV-03).
    ///
    /// Laying a line out is a plan-view job and inspecting one is not, and
    /// hunting for either by dragging is the sort of small friction nobody
    /// reports and everybody feels. The distance and the point being looked at
    /// are left alone — this is a change of angle, not of subject, so pressing
    /// it never loses the thing you were looking at.
    ///
    /// Top is clamped to <see cref="MinPitch"/> rather than being a true
    /// straight-down view: at exactly -90 degrees the yaw stops meaning
    /// anything and the camera's up vector becomes ambiguous, which makes the
    /// next drag snap to an angle nobody chose.
    /// </summary>
    public void ViewFrom(CameraPreset preset)
    {
        (_yawGoal, _pitchGoal) = preset switch
        {
            CameraPreset.Top => (_yawGoal, MinPitch),
            CameraPreset.Front => (0.0f, -0.18f),
            CameraPreset.Side => (-Mathf.Pi / 2.0f, -0.18f),
            _ => (-0.62f, -0.55f),      // the three-quarter view everything opens on
        };
        _settled = false;
    }

    /// <summary>
    /// Put the camera exactly here, now, with no chase. For filming
    /// (<c>--camera=</c>): a recording has to open on the shot, not swing
    /// into it. Angles are degrees, distance and target are metres; a yaw of
    /// 0 looks along -Z from +Z, positive yaw walks the camera toward +X, and
    /// a negative pitch looks down.
    /// </summary>
    public void Place(float yawDegrees, float pitchDegrees, float distance, Vector3 target)
    {
        Target = _targetGoal = target;
        Distance = _distanceGoal = Mathf.Clamp(distance, 0.3f, 25f);
        Yaw = _yawGoal = Mathf.DegToRad(yawDegrees);
        Pitch = _pitchGoal = Mathf.Clamp(Mathf.DegToRad(pitchDegrees), MinPitch, MaxPitch);
        _settled = true;
        // A scene load frames its whole line, and that can land after this
        // call. The shot that was asked for wins.
        _pinned = true;
        Apply();
    }

    private bool _pinned;

    public enum CameraPreset { Iso, Top, Front, Side }

    public override void _UnhandledInput(InputEvent @event)
    {
        if (@event is InputEventMouseMotion motion)
        {
            if (Input.IsMouseButtonPressed(MouseButton.Left))
            {
                _yawGoal -= motion.Relative.X * 0.006f;
                _pitchGoal = Mathf.Clamp(_pitchGoal - motion.Relative.Y * 0.006f, MinPitch, MaxPitch);
                _settled = false;
            }
            else if (Input.IsMouseButtonPressed(MouseButton.Middle))
            {
                var right = Transform.Basis.X;
                var up = Transform.Basis.Y;
                _targetGoal -= (right * motion.Relative.X + up * -motion.Relative.Y) * Distance * 0.0015f;
                _settled = false;
            }
        }
        else if (@event is InputEventMouseButton button && button.Pressed)
        {
            // Zoom in proportion to the current distance, so one wheel click
            // covers the same fraction of the view whether you are looking at a
            // whole line or at one sensor head. A fixed 0.4 m step crawled when
            // zoomed out and overshot the part entirely when zoomed in.
            if (button.ButtonIndex == MouseButton.WheelUp) Zoom(-0.16f);
            else if (button.ButtonIndex == MouseButton.WheelDown) Zoom(0.16f);
        }
    }

    private void Zoom(float fraction)
    {
        _distanceGoal = Mathf.Clamp(_distanceGoal * (1.0f + fraction), 0.6f, 25f);
        _settled = false;
    }

    public override void _Process(double delta)
    {
        if (_settled) return;

        // Exponential chase. Pow makes it independent of frame time: the same
        // fraction of the error is removed per second however often this runs.
        float t = 1.0f - Mathf.Pow(SettleFactor, (float)delta);

        Target = Target.Lerp(_targetGoal, t);
        Distance = Mathf.Lerp(Distance, _distanceGoal, t);
        Yaw = Mathf.Lerp(Yaw, _yawGoal, t);
        Pitch = Mathf.Lerp(Pitch, _pitchGoal, t);

        if (Target.DistanceTo(_targetGoal) < SettleEpsilon
            && Mathf.Abs(Distance - _distanceGoal) < SettleEpsilon
            && Mathf.Abs(Yaw - _yawGoal) < SettleEpsilon
            && Mathf.Abs(Pitch - _pitchGoal) < SettleEpsilon)
        {
            Target = _targetGoal;
            Distance = _distanceGoal;
            Yaw = _yawGoal;
            Pitch = _pitchGoal;
            _settled = true;
        }

        Apply();
    }

    private void Apply()
    {
        var offset = new Vector3(
            Mathf.Cos(Pitch) * Mathf.Sin(Yaw),
            -Mathf.Sin(Pitch),
            Mathf.Cos(Pitch) * Mathf.Cos(Yaw)) * Distance;
        Position = Target + offset;
        LookAt(Target, Vector3.Up);
    }
}
