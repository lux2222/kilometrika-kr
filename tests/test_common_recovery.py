import sys, unittest, tempfile, sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from parser import parse_activity
from db import Store

XML='''<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"><Activities><Activity Sport="Other"><Id>2020-01-02T03:04:05Z</Id><Lap StartTime="2020-01-02T03:04:05Z"><TotalTimeSeconds>60</TotalTimeSeconds><DistanceMeters>0</DistanceMeters><Calories>42</Calories><AverageHeartRateBpm><Value>110</Value></AverageHeartRateBpm><MaximumHeartRateBpm><Value>120</Value></MaximumHeartRateBpm><Intensity>Active</Intensity><TriggerMethod>Manual</TriggerMethod><Track><Trackpoint><Time>2020-01-02T03:04:05Z</Time><HeartRateBpm><Value>110</Value></HeartRateBpm></Trackpoint><Trackpoint><Time>2020-01-02T03:04:15Z</Time><HeartRateBpm><Value>120</Value></HeartRateBpm></Trackpoint></Track></Lap></Activity></Activities></TrainingCenterDatabase>'''
class CommonTest(unittest.TestCase):
 def test_shared_parse_save_recover(self):
  from recovery import recover_missing
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory);archive=p/'archive';archive.mkdir();f=archive/'unrelated-name.tcx';f.write_text(XML)
   a=parse_activity(str(f));s=Store(str(p/'tcx.db'));i=s.save(a,'original.tcx')
   self.assertEqual(a.category,'other');self.assertEqual(a.duration_s,60);self.assertEqual(len(s.get_trackpoints(i)),2)
   self.assertIsNone(s.get_trackpoints(i)[0]['lat'])
   with sqlite3.connect(s.path) as c:c.execute('UPDATE activities SET duration_s=0,hr_max=NULL WHERE id=?',(i,));c.execute('DELETE FROM trackpoints WHERE activity_id=?',(i,))
   before=s.get_activity(i);r=recover_missing(s,i,archive);self.assertEqual(r['status'],'recovered',r)
   self.assertEqual(s.get_activity(i)['duration_s'],60);self.assertEqual(s.get_activity(i)['calories'],before['calories']);self.assertEqual(len(s.get_trackpoints(i)),2)
   r=recover_missing(s,i,archive);self.assertEqual(len(s.get_trackpoints(i)),2);self.assertNotEqual(r['status'],'failed')
   with sqlite3.connect(s.path) as c:c.execute('UPDATE activities SET duration_s=0 WHERE id=?',(i,))
   f.write_text(XML+' ');r=recover_missing(s,i,archive);self.assertEqual(r['status'],'failed');self.assertEqual(s.get_activity(i)['duration_s'],0)
if __name__=='__main__': unittest.main()
