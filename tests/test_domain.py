import pytest
from domain import capacity, daily_allocation, variance

R={'id':'r1','start':'2026-01-01','end':None,'hours':40,'pattern':'[8,8,8,8,8,0,0]'}

def test_capacity_with_leave_and_overlapping_holiday():
    leave=[{'resource_id':'r1','start':'2026-11-13','end':'2026-11-13','hours':8,'approved':1}]
    dates=['2026-11-09','2026-11-10','2026-11-11','2026-11-12','2026-11-13']
    assert sum(capacity(R,d,leave) for d in dates)==32
    assert capacity(R,'2026-11-13',leave+leave)==0
    assert capacity(R,'2026-11-14')==0

def test_effective_dated_capacity():
    changes=[{'resource_id':'r1','effective':'2026-11-11','hours':20}]
    assert capacity(R,'2026-11-10',changes=changes)==8
    assert capacity(R,'2026-11-11',changes=changes)==4

def test_weekly_allocation_and_variance():
    assert daily_allocation({'hours':25,'unit':'week'},R)==5
    assert 40-25-20==-5
    assert variance(45,40)=={'hours':5,'percent':12.5}
    assert variance(8,0)['percent'] is None
