pub type Point = (f64, f64);

/// Evaluate cubic Bézier curve at parameter t in [0, 1].
pub fn cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: f64) -> Point {
    let u = 1.0 - t;
    let tt = t * t;
    let uu = u * u;
    let uuu = uu * u;
    let ttt = tt * t;

    let x = uuu * p0.0 + 3.0 * uu * t * p1.0 + 3.0 * u * tt * p2.0 + ttt * p3.0;
    let y = uuu * p0.1 + 3.0 * uu * t * p1.1 + 3.0 * u * tt * p2.1 + ttt * p3.1;
    (x, y)
}

/// Generate a human-like mouse path from start to end with S-curve easing.
pub fn generate_human_trajectory(start: Point, end: Point, steps: usize) -> Vec<Point> {
    let dx = end.0 - start.0;
    let dy = end.1 - start.1;
    let dist = (dx * dx + dy * dy).sqrt();

    if dist < 4.0 {
        return vec![end];
    }

    let actual_steps = steps.max(5).min(35);

    // Compute a perpendicular arc
    let norm = dist.max(1.0);
    let perp_x = -dy / norm;
    let perp_y = dx / norm;

    let arc_mag = dist * 0.15;
    let p0 = start;
    let p1 = (start.0 + dx * 0.3 + perp_x * arc_mag, start.1 + dy * 0.3 + perp_y * arc_mag);
    let p2 = (start.0 + dx * 0.75 + perp_x * (arc_mag * 0.5), start.1 + dy * 0.75 + perp_y * (arc_mag * 0.5));
    let p3 = end;

    let mut trajectory = Vec::with_capacity(actual_steps);
    for i in 1..=actual_steps {
        let raw_t = i as f64 / actual_steps as f64;
        // S-curve easing: 3*t^2 - 2*t^3
        let t = 3.0 * (raw_t * raw_t) - 2.0 * (raw_t * raw_t * raw_t);
        trajectory.push(cubic_bezier(p0, p1, p2, p3, t));
    }

    trajectory
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cubic_bezier_endpoints() {
        let p0 = (0.0, 0.0);
        let p1 = (10.0, 20.0);
        let p2 = (30.0, 40.0);
        let p3 = (100.0, 100.0);

        let start = cubic_bezier(p0, p1, p2, p3, 0.0);
        assert!((start.0 - 0.0).abs() < 1e-6);
        assert!((start.1 - 0.0).abs() < 1e-6);

        let end = cubic_bezier(p0, p1, p2, p3, 1.0);
        assert!((end.0 - 100.0).abs() < 1e-6);
        assert!((end.1 - 100.0).abs() < 1e-6);
    }

    #[test]
    fn test_human_trajectory_generation() {
        let traj = generate_human_trajectory((10.0, 10.0), (200.0, 300.0), 20);
        assert!(!traj.is_empty());
        let last = traj.last().unwrap();
        assert!((last.0 - 200.0).abs() < 1e-5);
        assert!((last.1 - 300.0).abs() < 1e-5);
    }
}

