import unittest

from airops.normalization import normalize_airplanes, normalize_opensky


class NormalizeTests(unittest.TestCase):
    def test_airplanes_live_preserves_reported_identity_and_valid_position(self):
        row = normalize_airplanes({"hex": "ABC123", "flight": "AIC42 ", "r": "VT-ABC", "t": "A320", "lat": 18.5, "lon": 73.8, "alt_baro": "ground", "gs": 0})
        self.assertEqual(row["icao"], "abc123")
        self.assertEqual(row["callsign"], "AIC42")
        self.assertEqual(row["registration"], "VT-ABC")
        self.assertEqual(row["altitude_ft"], 0)

    def test_invalid_coordinates_are_discarded(self):
        self.assertIsNone(normalize_airplanes({"hex": "abc", "lat": 92, "lon": 0}))
        self.assertIsNone(normalize_airplanes({"hex": "abc", "lat": 0, "lon": 181}))

    def test_opensky_units_are_converted(self):
        state = ["abc123", "CALL ", "India", 0, 0, 73.8, 18.5, 1000, False, 100, 270, 2, None, None, "1200", False, 0]
        row = normalize_opensky(state)
        self.assertEqual(row["callsign"], "CALL")
        self.assertEqual(row["altitude_ft"], 3281)
        self.assertEqual(row["ground_speed_kt"], 194)
        self.assertIsNone(row["registration"])


if __name__ == "__main__":
    unittest.main()
