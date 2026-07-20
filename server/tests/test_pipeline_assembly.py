import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pipeline

def test_round_offset_dedups_across_demos():
    a = [{"side":"CT","rtype":"Buy","official_num":1,"path":[],"grenades":[]}]
    b = [{"side":"CT","rtype":"Buy","official_num":1,"path":[],"grenades":[]}]
    ra = pipeline.assemble_round_offset(a, 0)
    rb = pipeline.assemble_round_offset(b, 1)
    assert ra[0]["official_num"] == 1
    assert rb[0]["official_num"] == 1001   # offset by dem_idx*1000
