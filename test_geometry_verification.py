"""Acceptance invariants: rotation and a sampled flat crest must not mislead QA."""
import math
import unittest

from verify_geometry import section_hull, thread_period


class GeometryVerificationTests(unittest.TestCase):
    def test_hexagon_section_is_independent_of_rotation(self):
        for angle in (0, math.pi/6, .247):
            radius = 4/math.cos(math.pi/6)
            points = [(radius*math.cos(angle+i*math.pi/3), radius*math.sin(angle+i*math.pi/3)) for i in range(6)]
            triangles = []
            for a,b in zip(points,points[1:]+points[:1]):
                a0,b0 = (*a,0),(*b,0)
                a1,b1 = (*a,4.7),(*b,4.7)
                triangles.extend([(a0,b0,b1),(a0,b1,a1)])
            hull = section_hull(triangles,2.35)
            self.assertEqual(len(hull),6)
            for a,b in zip(hull,hull[1:]+hull[:1]):
                self.assertAlmostEqual(abs(a[0]*b[1]-a[1]*b[0])/math.dist(a,b),4,places=6)

    def test_thread_period_uses_profile_instead_of_noisy_crest_peaks(self):
        values = [min(2.49,2.1+.4*(1+math.cos(2*math.pi*(.75+i*.02)/.8))/2)+(-1)**i*1e-15 for i in range(160)]
        actual,_ = thread_period(values)
        self.assertAlmostEqual(actual,.8)

    def test_wrong_pitch_and_smooth_bore_are_not_accepted_as_point_eight(self):
        actual,_ = thread_period([2.1+.4*math.cos(2*math.pi*i*.02/.6) for i in range(160)])
        self.assertAlmostEqual(actual,.6)
        self.assertEqual(thread_period([2.1]*160),(None,[]))


if __name__ == "__main__":
    unittest.main()
