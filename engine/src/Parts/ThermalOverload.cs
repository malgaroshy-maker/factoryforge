using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A thermal overload relay's bimetal: an inverse-time trip curve that trips on
/// current <em>and time</em>, and stays tripped until somebody resets it.
///
/// Extracted from <see cref="MotorStarter"/> (IP-17) so the star-delta starter
/// trips on exactly the same curve rather than on a second copy of it that
/// could drift. The arithmetic is unchanged: heating goes as the square of the
/// current over the trip setting, which is what makes an overload inverse-time
/// — six times the trip current fills the element in the trip-class time, twice
/// the trip current takes about twelve times as long, and ten percent over
/// takes a very long while indeed. Below the trip setting the element cools at
/// a third of that rate, so a line that starts, runs a while and starts again
/// does not accumulate its way to a trip, while repeated starting still does.
/// </summary>
public sealed class ThermalOverload
{
    /// <summary>The multiple of the trip current a trip class is defined at:
    /// Class 10 trips in 10 s at six times the setting, which is roughly where
    /// a motor's starting inrush sits.</summary>
    public const float ClassMultiple = 6.0f;

    /// <summary>How far through its trip curve the element is, 0..1.</summary>
    public float State { get; private set; }

    /// <summary>Has the element opened? Latched: an overload stays tripped
    /// until reset, which is the whole difference between a protective device
    /// and a fuse that heals.</summary>
    public bool IsTripped { get; private set; }

    /// <summary>
    /// One tick of the bimetal.
    /// </summary>
    /// <param name="current">The current through the element, amps.</param>
    /// <param name="tripAmps">The trip setting, amps. Floored, because a
    /// setting of zero would divide by nothing and hand the tag table an
    /// infinity (HP-23).</param>
    /// <param name="tripTime">The trip class: seconds to trip at
    /// <see cref="ClassMultiple"/> times the setting.</param>
    /// <returns>True on the one tick the element trips.</returns>
    public bool Step(float current, float tripAmps, float tripTime, float delta)
    {
        float ratio = current / Mathf.Max(tripAmps, 0.01f);
        float curveTime = Mathf.Max(tripTime, 0.05f);

        // Heat at (I/Itrip)^2 - 1 per second, scaled so ratio == ClassMultiple
        // fills the element in tripTime -- that is what the trip class means.
        float scale = (ClassMultiple * ClassMultiple - 1.0f) * curveTime;
        float rate = (ratio * ratio - 1.0f) / scale;
        if (rate < 0.0f) rate /= 3.0f;

        State = Mathf.Clamp(State + rate * delta, 0.0f, 1.0f);

        if (State < 1.0f || IsTripped) return false;
        IsTripped = true;
        return true;
    }

    /// <summary>The reset button on the front of the relay.</summary>
    public void Reset()
    {
        IsTripped = false;
        State = 0.0f;
    }
}
