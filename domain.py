"""Date-only planning rules. Reporting dates are explicit Australia/Sydney dates."""
import json
from datetime import date, timedelta

def days(start,end):
    current=date.fromisoformat(start); final=date.fromisoformat(end)
    while current<=final:
        yield current.isoformat()
        current+=timedelta(days=1)

def scheduled(resource,day,changes=()):
    if day<resource['start'] or (resource['end'] and day>resource['end']): return 0
    pattern=json.loads(resource['pattern']); weekday=date.fromisoformat(day).weekday()
    hours=pattern[weekday]
    effective=sorted((c for c in changes if c['resource_id']==resource['id'] and c['effective']<=day),key=lambda c:c['effective'],reverse=True)
    if effective:
        hours=hours*effective[0]['hours']/resource['hours'] if resource['hours'] else (effective[0]['hours']/5 if weekday<5 else 0)
    return hours

def capacity(resource,day,leave=(),changes=()):
    # Date-only overlapping non-working events represent the same unavailable hours.
    unavailable=max((l['hours'] for l in leave if l['approved'] and l['resource_id']==resource['id'] and l['start']<=day<=l['end']),default=0)
    return max(0,scheduled(resource,day,changes)-unavailable)

def daily_allocation(task,resource):
    if task.get('unit','day')=='day': return task['hours']
    workdays=sum(h>0 for h in json.loads(resource['pattern'])) or 5
    return task['hours']/workdays

def variance(actual,planned):
    return {'hours':actual-planned,'percent':(actual-planned)/planned*100 if planned else None}
