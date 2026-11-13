from datetime import datetime, timezone
from pathlib import Path

import pytest

from ridelay.activity import UnsupportedFormatError, load_activity
from ridelay.gpx_parser import GpxError, parse_gpx
from ridelay.processing import build_telemetry
from ridelay.tcx_parser import TcxError, parse_tcx

TCX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
  xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">
  <Activities>
    <Activity Sport="Biking">
      <Id>2026-11-07T00:55:01.000Z</Id>
      <Lap StartTime="2026-11-07T00:55:01.000Z">
        <Track>
{points}
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""


def _write_tcx(tmp_path: Path, points_xml: str, filename: str = "activity.tcx") -> Path:
    p = tmp_path / filename
    p.write_text(TCX_TEMPLATE.format(points=points_xml), encoding="utf-8")
    return p


def test_parse_tcx_basic(tmp_path):
    points_xml = """
          <Trackpoint>
            <Time>2026-11-07T00:55:01.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302552</LatitudeDegrees>
              <LongitudeDegrees>138.944884</LongitudeDegrees>
            </Position>
            <AltitudeMeters>444.0</AltitudeMeters>
            <HeartRateBpm>
              <Value>84</Value>
            </HeartRateBpm>
            <Cadence>0</Cadence>
            <Extensions>
              <ns3:TPX>
                <ns3:Speed>0.0</ns3:Speed>
                <ns3:Watts>150</ns3:Watts>
              </ns3:TPX>
            </Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-11-07T00:55:02.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302560</LatitudeDegrees>
              <LongitudeDegrees>138.944890</LongitudeDegrees>
            </Position>
            <AltitudeMeters>445.0</AltitudeMeters>
            <HeartRateBpm>
              <Value>85</Value>
            </HeartRateBpm>
            <Cadence>60</Cadence>
            <Extensions>
              <ns3:TPX>
                <ns3:Speed>2.5</ns3:Speed>
                <ns3:Watts>200</ns3:Watts>
              </ns3:TPX>
            </Extensions>
          </Trackpoint>
    """
    file_path = _write_tcx(tmp_path, points_xml)
    pts = parse_tcx(file_path)

    assert len(pts) == 2
    assert pts[0].time == datetime(2026, 11, 7, 0, 55, 1, tzinfo=timezone.utc)
    assert pts[0].lat == pytest.approx(35.302552)
    assert pts[0].lon == pytest.approx(138.944884)
    assert pts[0].ele == pytest.approx(444.0)
    assert pts[0].hr == 84
    assert pts[0].cad == 0
    assert pts[0].power == 150

    assert pts[1].time == datetime(2026, 11, 7, 0, 55, 2, tzinfo=timezone.utc)
    assert pts[1].hr == 85
    assert pts[1].cad == 60
    assert pts[1].power == 200


def test_parse_tcx_requires_two_points(tmp_path):
    points_xml = """
          <Trackpoint>
            <Time>2026-11-07T00:55:01.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302552</LatitudeDegrees>
              <LongitudeDegrees>138.944884</LongitudeDegrees>
            </Position>
          </Trackpoint>
    """
    file_path = _write_tcx(tmp_path, points_xml)
    with pytest.raises(TcxError):
        parse_tcx(file_path)


def test_parse_tcx_invalid_root(tmp_path):
    invalid = tmp_path / "invalid.tcx"
    invalid.write_text("<InvalidRoot></InvalidRoot>", encoding="utf-8")
    with pytest.raises(TcxError):
        parse_tcx(invalid)


def test_parse_tcx_dedup_and_sort(tmp_path):
    points_xml = """
          <Trackpoint>
            <Time>2026-11-07T00:55:02.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302560</LatitudeDegrees>
              <LongitudeDegrees>138.944890</LongitudeDegrees>
            </Position>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-11-07T00:55:01.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302552</LatitudeDegrees>
              <LongitudeDegrees>138.944884</LongitudeDegrees>
            </Position>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-11-07T00:55:02.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302570</LatitudeDegrees>
              <LongitudeDegrees>138.944900</LongitudeDegrees>
            </Position>
          </Trackpoint>
    """
    file_path = _write_tcx(tmp_path, points_xml)
    pts = parse_tcx(file_path)
    assert len(pts) == 2
    assert pts[0].time == datetime(2026, 11, 7, 0, 55, 1, tzinfo=timezone.utc)
    assert pts[1].time == datetime(2026, 11, 7, 0, 55, 2, tzinfo=timezone.utc)
    # 先勝ちで 35.302560 が残る
    assert pts[1].lat == pytest.approx(35.302560)


def test_load_activity_auto_detection(tmp_path):
    # TCX with .tcx
    tcx_file = _write_tcx(tmp_path, """
          <Trackpoint>
            <Time>2026-11-07T00:55:01.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302552</LatitudeDegrees>
              <LongitudeDegrees>138.944884</LongitudeDegrees>
            </Position>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-11-07T00:55:02.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302560</LatitudeDegrees>
              <LongitudeDegrees>138.944890</LongitudeDegrees>
            </Position>
          </Trackpoint>
    """, filename="test.tcx")

    pts_tcx = load_activity(tcx_file)
    assert len(pts_tcx) == 2

    # TCX without extension (inspect root tag)
    tcx_no_ext = tmp_path / "test_no_ext"
    tcx_no_ext.write_text(tcx_file.read_text(encoding="utf-8"), encoding="utf-8")
    pts_tcx_no_ext = load_activity(tcx_no_ext)
    assert len(pts_tcx_no_ext) == 2

    # Unsupported format
    unknown = tmp_path / "unknown.xml"
    unknown.write_text("<SomeRoot><Child/></SomeRoot>", encoding="utf-8")
    with pytest.raises(UnsupportedFormatError):
        load_activity(unknown)


def test_tcx_telemetry_power(tmp_path):
    points_xml = """
          <Trackpoint>
            <Time>2026-11-07T00:55:01.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302552</LatitudeDegrees>
              <LongitudeDegrees>138.944884</LongitudeDegrees>
            </Position>
            <Extensions>
              <ns3:TPX><ns3:Watts>100</ns3:Watts></ns3:TPX>
            </Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-11-07T00:55:02.000Z</Time>
            <Position>
              <LatitudeDegrees>35.302560</LatitudeDegrees>
              <LongitudeDegrees>138.944890</LongitudeDegrees>
            </Position>
            <Extensions>
              <ns3:TPX><ns3:Watts>200</ns3:Watts></ns3:TPX>
            </Extensions>
          </Trackpoint>
    """
    file_path = _write_tcx(tmp_path, points_xml)
    pts = load_activity(file_path)
    tel = build_telemetry(pts, smooth_power=1.0)
    assert tel.has["power"]
    assert tel.held("power", 0.0) == 100.0
    assert tel.held("power", 1.0) == 200.0
