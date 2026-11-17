using System.Globalization;
using System.Numerics;
using System.Text;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace PhoneUseMirror;

/// <summary>
/// iOS's continuous ("squircle") corner: the curve UIKit draws for a rounded rectangle and an iPhone's
/// screen and body follow. Unlike a circular arc it starts bending 1.53 radii from the corner and eases
/// into it, so there's no visible point where the straight edge stops. The control points are the
/// ones PaintCode reverse-engineered from UIBezierPath (iOS 7 and later).
///
/// A corner's points are (X, Y) in units of its radius: X the distance in from the edge the curve ends
/// on, Y the distance in from the edge it starts on. It runs from (Extent, 0) to (0, Extent),
/// symmetric about X = Y.
/// </summary>
public static class ContinuousCorner
{
    /// <summary>How far from the corner the curve starts, in radii.</summary>
    public const double Extent = 1.52866483;

    // The first half of the corner: a cubic, a short line, then the symmetric middle cubic.
    // The second half mirrors the first.
    static readonly (Vector2 c1, Vector2 c2, Vector2 to)[] FirstHalf =
    {
        (new(1.08849323f, 0f), new(0.86840689f, 0f), new(0.66993427f, 0.06549600f)),
        (default, default, new(0.63149399f, 0.07491100f)),  // a line
        (new(0.37282392f, 0.16905899f), new(0.16906013f, 0.37282401f), new(0.07491176f, 0.63149399f)),
    };

    /// <summary>The whole corner as (c1, c2, to) cubics and lines (c1 == c2 == default), from (Extent, 0).</summary>
    public static readonly (Vector2 c1, Vector2 c2, Vector2 to, bool line)[] Segments = BuildSegments();

    /// <summary>The corner sampled finely, from (Extent, 0) to (0, Extent): for distances.</summary>
    public static readonly Vector2[] Points = Sample();

    static (Vector2, Vector2, Vector2, bool)[] BuildSegments()
    {
        static Vector2 Swap(Vector2 v) => new(v.Y, v.X);
        var list = new List<(Vector2, Vector2, Vector2, bool)>();
        foreach (var (c1, c2, to) in FirstHalf) list.Add((c1, c2, to, c1 == default && c2 == default));
        // the mirror image, backwards: each piece from its end to its start, X and Y swapped
        var starts = new List<Vector2> { new((float)Extent, 0) };
        foreach (var s in FirstHalf) starts.Add(s.to);
        for (int i = FirstHalf.Length - 2; i >= 0; i--)
        {
            // FirstHalf[i] runs starts[i] -> starts[i+1]; its mirror runs Swap(starts[i+1]) -> Swap(starts[i])
            var (c1, c2, _) = FirstHalf[i];
            bool line = c1 == default && c2 == default;
            list.Add(line ? (default, default, Swap(starts[i]), true) : (Swap(c2), Swap(c1), Swap(starts[i]), false));
        }
        return list.ToArray();
    }

    static Vector2[] Sample()
    {
        const int steps = 14;
        var pts = new List<Vector2> { new((float)Extent, 0) };
        var from = pts[0];
        foreach (var (c1, c2, to, line) in Segments)
        {
            if (line) pts.Add(to);
            else
                for (int i = 1; i <= steps; i++)
                {
                    float t = i / (float)steps, u = 1 - t;
                    pts.Add(u * u * u * from + 3 * u * u * t * c1 + 3 * u * t * t * c2 + t * t * t * to);
                }
            from = to;
        }
        return pts.ToArray();
    }

    /// <summary>The radius actually drawn: a corner can't reach past the middle of a side.</summary>
    public static double Limit(double r, double w, double h) =>
        Math.Max(0, Math.Min(r, Math.Min(w, h) / 2 / Extent));

    /// <summary>A w x h rectangle at (x, y) with continuous corners of radius r.</summary>
    public static PathGeometry Rect(double x, double y, double w, double h, double r)
    {
        r = Limit(r, w, h);
        double e = r * Extent;
        // each corner: where it is, and the directions of its X and Y (clockwise from the top right)
        var corners = new (Point at, Vector2 ux, Vector2 uy)[]
        {
            (new(x + w, y), new(-1, 0), new(0, 1)),      // top right: starts on the top edge
            (new(x + w, y + h), new(0, -1), new(-1, 0)), // bottom right: starts on the right edge
            (new(x, y + h), new(1, 0), new(0, -1)),      // bottom left: starts on the bottom edge
            (new(x, y), new(0, 1), new(1, 0)),           // top left: starts on the left edge
        };
        Point P((Point at, Vector2 ux, Vector2 uy) c, Vector2 v) =>
            new(c.at.X + (c.ux.X * v.X + c.uy.X * v.Y) * r, c.at.Y + (c.ux.Y * v.X + c.uy.Y * v.Y) * r);

        var figure = new PathFigure { StartPoint = new Point(x + w - e, y), IsClosed = true, IsFilled = true };
        foreach (var c in corners)
        {
            figure.Segments.Add(new LineSegment { Point = P(c, new Vector2((float)Extent, 0)) });
            foreach (var (c1, c2, to, line) in Segments)
                figure.Segments.Add(line
                    ? new LineSegment { Point = P(c, to) }
                    : new BezierSegment { Point1 = P(c, c1), Point2 = P(c, c2), Point3 = P(c, to) });
        }
        var g = new PathGeometry();
        g.Figures.Add(figure);
        return g;
    }

    /// <summary>Signed distance to a rectangle of half size b with continuous corners of radius r,
    /// centred on the origin; negative inside.</summary>
    public static float Sd(Vector2 p, Vector2 b, float r)
    {
        r = (float)Limit(r, 2 * b.X, 2 * b.Y);
        float e = r * (float)Extent;
        var a = Vector2.Abs(p);
        float X = b.X - a.X, Y = b.Y - a.Y;  // in from the side edges, and from the top and bottom
        if (X >= e && Y >= e) return -Math.Min(X, Y);
        if (Y >= e) return -X;
        if (X >= e) return -Y;
        if (r <= 0) return X < 0 && Y < 0 ? new Vector2(X, Y).Length() : -Math.Min(X, Y);

        // in a corner's square: the nearest point of the curve, inside when on the corner's inner side
        var q = new Vector2(X, Y) / r;
        float best = float.MaxValue, side = 1;
        var pts = Points;
        for (int i = 0; i + 1 < pts.Length; i++)
        {
            var s = pts[i];
            var d = pts[i + 1] - s;
            var w = q - s;
            float t = Math.Clamp(Vector2.Dot(w, d) / Math.Max(d.LengthSquared(), 1e-12f), 0, 1);
            float dist = (w - d * t).LengthSquared();
            if (dist < best)
            {
                best = dist;
                side = d.X * w.Y - d.Y * w.X;  // < 0: on the inner side
            }
        }
        float distance = MathF.Sqrt(best) * r;
        return side < 0 ? -distance : distance;
    }

    /// <summary>The same distance for the agent glow's shader (HLSL): the sampled corner as a constant array.</summary>
    public static string Hlsl()
    {
        var sb = new StringBuilder();
        var inv = CultureInfo.InvariantCulture;
        sb.Append($"#define CORNER_N {Points.Length}\n");
        sb.Append($"#define CORNER_EXTENT {Extent.ToString("R", inv)}\n");
        sb.Append("static const float2 Corner[CORNER_N] = {");
        sb.Append(string.Join(",", Points.Select(v => $"float2({v.X.ToString("R", inv)},{v.Y.ToString("R", inv)})")));
        sb.Append("};\n");
        sb.Append("""
// Signed distance to a rectangle of half size b with iOS's continuous corners of radius r (ContinuousCorner.Sd).
float sdContinuous(float2 p, float2 b, float r)
{
    r = max(0, min(r, min(b.x, b.y) / CORNER_EXTENT));
    float e = r * CORNER_EXTENT;
    float2 a = abs(p);
    float X = b.x - a.x, Y = b.y - a.y;
    if (X >= e && Y >= e) return -min(X, Y);
    if (Y >= e) return -X;
    if (X >= e) return -Y;
    if (r <= 0) return (X < 0 && Y < 0) ? length(float2(X, Y)) : -min(X, Y);
    float2 q = float2(X, Y) / r;
    float best = 1e20, side = 1;
    [loop] for (int i = 0; i + 1 < CORNER_N; i++)
    {
        float2 s = Corner[i], d = Corner[i + 1] - s, w = q - s;
        float t = saturate(dot(w, d) / max(dot(d, d), 1e-12));
        float2 v = w - d * t;
        float dd = dot(v, v);
        if (dd < best) { best = dd; side = d.x * w.y - d.y * w.x; }
    }
    float dist = sqrt(best) * r;
    return side < 0 ? -dist : dist;
}

""");
        return sb.ToString();
    }
}
