from __future__ import annotations
import unittest
from image_transfer import ImageRateMeter, ImageTransferPolicy, OriginalSourcePlan

class RateMeterTests(unittest.TestCase):
    def meter(self): return ImageRateMeter(0,ImageTransferPolicy())
    def test_recent_rate_recovers_instead_of_retaining_early_slow_average(self):
        m=self.meter();received=0
        for second in range(1,13):
            received+=16*1024;m.update(second,received)
        for second in range(13,17):
            received+=512*1024;m.update(second,received)
        self.assertAlmostEqual(m.speed,512*1024)
        self.assertGreater(m.speed,received/16*2)
    def test_grace_and_two_consecutive_slow_windows(self):
        m=self.meter()
        for second in range(1,11):
            m.update(second,second*16*1024)
            self.assertFalse(m.should_switch(second*16*1024,2*1024*1024))
        m.update(11,11*16*1024)
        self.assertTrue(m.should_switch(11*16*1024,2*1024*1024))
    def test_one_fast_window_resets_slow_count(self):
        m=self.meter();received=0
        for second in range(1,8):received+=16*1024;m.update(second,received)
        self.assertEqual(m.slow_count,1)
        for second in range(8,12):received+=256*1024;m.update(second,received)
        self.assertEqual(m.slow_count,0)
    def test_complete_and_nearly_complete_images_do_not_switch(self):
        m=self.meter()
        for second in range(1,12):m.update(second,second*16*1024)
        size=11*16*1024
        self.assertFalse(m.should_switch(size,size))
        self.assertFalse(m.should_switch(size,size+32*1024))
        self.assertTrue(m.should_switch(size,None))
    def test_fast_images_never_switch(self):
        m=self.meter()
        for second in range(1,21):
            m.update(second,second*512*1024)
            self.assertFalse(m.should_switch(second*512*1024,None))
    def test_sampling_is_bounded_on_a_fast_stream(self):
        m=self.meter()
        for sample in range(10000):m.update(sample*.01,sample*16384)
        self.assertLessEqual(len(m.samples),33)
    def test_clock_with_no_elapsed_time_has_no_fake_infinite_rate(self):
        m=self.meter();self.assertIsNone(m.update(0,123))

class SourcePlanTests(unittest.TestCase):
    def test_other_originals_precede_retry_and_preview(self):
        plan=OriginalSourcePlan(4,(0,1,2),{})
        calls=list(plan.attempts())
        self.assertEqual([i for i,_,_ in calls],[0,1,2,0,1,2,3,3])
    def test_bounded_slow_probe_revisits_best_original_before_preview(self):
        plan=OriginalSourcePlan(6,(0,1,2,3,4),{},3);calls=[]
        for index,attempt,watch in plan.attempts():
            calls.append((index,attempt,watch))
            if watch:plan.mark_slow(index,[16,24,12][index]*1024)
        self.assertEqual(calls[:4],[(0,1,True),(1,1,True),(2,1,True),(1,2,False)])
        self.assertEqual([i for i,_,_ in calls].count(0),2)
        self.assertEqual(len(calls),12)
        self.assertTrue(all(sum(i==index for i,_,_ in calls)==2 for index in range(6)))
        self.assertEqual(calls[-2:],[(5,1,False),(5,2,False)])
    def test_successful_node_rank_can_be_used_before_untried_node(self):
        plan=OriginalSourcePlan(4,(0,1,2),{0:(1,0,0),1:(0,-512000,1),2:(0,0,2)})
        self.assertEqual(next(plan.attempts())[0],1)
    def test_one_original_has_no_low_speed_abort(self):
        self.assertEqual(list(OriginalSourcePlan(2,(0,),{}).attempts()),[(0,1,False),(0,2,False),(1,1,False),(1,2,False)])

if __name__=='__main__':unittest.main()
