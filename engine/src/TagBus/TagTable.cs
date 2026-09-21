using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;

namespace FactoryForge.TagBus;

/// <summary>
/// The authoritative tag collection. Mirrors TagTable in
/// sidecar/factoryforge_sidecar/tags.py.
/// </summary>
public sealed class TagTable : IEnumerable<Tag>
{
    private readonly Dictionary<string, Tag> _tags = new();
    private readonly Dictionary<string, object> _forced = new();

    public int Count => _tags.Count;

    public void Add(Tag tag)
    {
        if (_tags.ContainsKey(tag.Id))
            throw new System.ArgumentException($"duplicate tag id {tag.Id}");
        _tags[tag.Id] = tag;
    }

    public bool Contains(string id) => _tags.ContainsKey(id);

    /// <summary>
    /// Drop a tag and any force pinned on it. Used when a part is deleted: its
    /// tags have to go with it, or re-placing a part of the same type collides
    /// with the ids the old one left behind and <see cref="Add"/> throws.
    /// </summary>
    public bool Remove(string id)
    {
        _forced.Remove(id);
        return _tags.Remove(id);
    }

    public Tag? Get(string id) => _tags.TryGetValue(id, out var t) ? t : null;

    public Tag this[string id] => _tags[id];

    public IEnumerable<Tag> ByKind(TagKind kind) => _tags.Values.Where(t => t.Kind == kind);

    /// <summary>
    /// Set a tag's value. Returns true if it actually changed.
    /// A forced tag absorbs the write silently — the underlying value updates so
    /// that clearing the force reveals something sensible, but the observable
    /// value stays pinned and no change is reported.
    ///
    /// A value that does not meaningfully differ is <em>not stored</em>
    /// (HP-18.3). Saying "nothing changed" and then storing the new value
    /// anyway let the reference creep by a hair a scan, so a float drifting
    /// 1e-9 per scan crossed the epsilon on the very next comparison and
    /// published on every single scan — exactly the traffic the epsilon exists
    /// to prevent. Coercion still happens first, so a value this tag cannot
    /// hold is rejected whether or not it would have changed anything.
    /// </summary>
    public bool Set(string id, object value)
    {
        var tag = _tags[id];
        var coerced = tag.Coerce(value);
        bool changed = tag.Differs(coerced);
        if (changed) tag.Set(coerced);
        return !_forced.ContainsKey(id) && changed;
    }

    public bool Force(string id, object value)
    {
        var tag = _tags[id];
        var coerced = tag.Coerce(value);
        var wasVisible = Visible(id);
        _forced[id] = coerced;
        return !Equals(coerced, wasVisible);
    }

    public bool ClearForce(string id)
    {
        if (!_forced.Remove(id, out var pinned)) return false;
        return _tags[id].Differs(pinned);
    }

    /// <summary>Release every forced tag at once (UX-41) — the toolbar's
    /// "release all" chip, so a tag left forced from an earlier session does
    /// not sit there silently outlasting the reason it was forced.</summary>
    public void ClearAllForces() => _forced.Clear();

    public bool IsForced(string id) => _forced.ContainsKey(id);

    /// <summary>Whether anything is currently forced — the idle hint (FF-23)
    /// uses this to tell "nobody has touched this scene yet" apart from
    /// "somebody is driving it by hand from the inspector".</summary>
    public bool AnyForced => _forced.Count > 0;

    /// <summary>How many tags are currently forced — the toolbar's chip shows
    /// this count so a tag left forced is not just invisible until the PLC
    /// that connects later mysteriously loses the argument (UX-41).</summary>
    public int ForcedCount => _forced.Count;

    /// <summary>
    /// <see cref="Contains"/> then <see cref="Visible"/> collapsed into one
    /// dictionary probe instead of two — the editor's physics dispatch calls
    /// this pattern several times per part, every tick. See FF-15.
    /// </summary>
    public bool TryGetVisible(string id, out object value)
    {
        if (!_tags.TryGetValue(id, out var tag)) { value = null!; return false; }
        value = _forced.TryGetValue(id, out var forced) ? forced : tag.Value;
        return true;
    }

    /// <summary><see cref="Contains"/> then <see cref="Set"/> collapsed into
    /// one dictionary probe. Returns false, without writing, if the tag does
    /// not exist. See FF-15.</summary>
    public bool TrySet(string id, object value)
    {
        if (!_tags.TryGetValue(id, out var tag)) return false;
        var coerced = tag.Coerce(value);
        bool changed = tag.Differs(coerced);
        if (changed) tag.Set(coerced);
        return !_forced.ContainsKey(id) && changed;
    }

    /// <summary>The value the outside world sees, honouring any force.</summary>
    public object Visible(string id) =>
        _forced.TryGetValue(id, out var v) ? v : _tags[id].Value;

    public JsonArray ToJson()
    {
        var arr = new JsonArray();
        foreach (var tag in _tags.Values)
            arr.Add(tag.ToJson(Visible(tag.Id), IsForced(tag.Id)));
        return arr;
    }

    public IEnumerator<Tag> GetEnumerator() => _tags.Values.GetEnumerator();
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
}
