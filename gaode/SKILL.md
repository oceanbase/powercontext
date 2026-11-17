---
name: amap-nearby-explorer
description: Use AMap POI search and walking routes to recommend nearby places that satisfy a user's category, walking-time, count, and ordering constraints. Use for requests such as finding nearby cafes, restaurants, parks, or other destinations from a supplied origin.
---

# AMap Nearby Explorer

Recommend real places from AMap data and verify every recommendation with a
walking route. Treat explicit limits as hard constraints, and do not infer
facts that the tools did not return.

## Understand the Request

Extract:

- the city and the supplied origin coordinate;
- the requested place category or search keyword;
- the maximum walking time, if any;
- the maximum result count, capped at 3;
- ordering preferences such as "nearest first";
- other preferences, separating verifiable constraints from subjective ones.

Coordinates use `longitude,latitude`. Keep the supplied AMap coordinate as the
origin for every route. If the task genuinely lacks a coordinate, resolve the
named origin with `maps_geo` and the city before searching. Ask for clarification
only when the origin or a hard requirement cannot be determined.

## Find Candidates

1. Call `maps_around_search` with:
   - `location`: the origin coordinate;
   - `keywords`: a concise place category, not the user's full sentence;
   - `radius`: a practical candidate radius. When there is a walking-time cap,
     use roughly `minutes * 100` meters, bounded to 1000-5000 meters. Otherwise,
     start near 3000 meters.
2. Keep POIs whose returned name or type is consistent with the requested
   category. Deduplicate by POI ID. Do not treat a keyword appearing in an
   unrelated place name as proof of category.
3. Build a candidate pool larger than the requested result count, normally up
   to 8 plausible POIs. For each candidate, use the location returned by the
   search result. If location or adequate type/address data is missing, call
   `maps_search_detail` with its POI ID. Never invent a destination coordinate.

If a strict time limit yields too few results, a second search may use a nearby
keyword synonym or a slightly larger radius, but the route-time filter must not
be relaxed.

## Verify Walking Routes

For each plausible candidate, call `maps_direction_walking` with the same
`origin` and that POI's `destination` coordinate. Each POI is an independent
alternative; never chain destinations into a multi-stop route.

From returned walking paths, select the path with the shortest duration, using
distance as the tie-breaker. Preserve the returned distance in meters and
duration in seconds as the evidence for filtering and ordering.

- Apply a time limit to the raw duration: include a place only when
  `duration <= limit_minutes * 60`.
- Do not substitute straight-line distance, search radius, or an estimated
  walking speed for route duration.
- Exclude candidates without a successful walking route. Retry a transient
  failed tool call once; after that, report the limitation instead of guessing.

## Select Recommendations

Apply every hard constraint before ranking. Return no more than the requested
count and never more than 3 places.

For "nearest" or similar preferences, sort by verified walking duration, then
verified walking distance. Apply another preference only when AMap returned
data that supports it. Otherwise label that preference as unverified rather
than using it to rank results.

Recommendation reasons must cite available evidence such as matching place
type, verified shorter walking time, or address context. Do not claim that a
place is quiet, uncrowded, open, popular, highly rated, or has short queues
unless the tool output explicitly supports that claim.

## Answer

Respond concisely in the user's language. Use a numbered list or table with:

| Place | Address | Walking distance | Walking time | Reason |
|---|---|---:|---:|---|

Convert route seconds to an easy-to-read approximate duration without rounding
down a hard limit, and keep distances clearly labeled as walking distances.
Mention that each route starts from the user's origin.

If fewer qualifying places exist, return only the verified matches and explain
why the requested count was not reached. If no place qualifies or an AMap tool
remains unavailable after one retry, say so directly and suggest a concrete
change such as increasing the time limit, radius, or broadening the category.
Mark unsupported preference details as "unverified". Never expose or request an
AMap API key; the evaluation environment supplies the tools and credentials.
