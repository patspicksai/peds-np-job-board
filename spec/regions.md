# Regions (controlled vocabulary)

Every hospital gets exactly one `region` from this list. Spelling must match exactly. `hotspot: true` marks areas the owner is actively considering; it is set automatically from the region.

| region | states | hotspot | notes |
|---|---|---|---|
| Portland ME | ME | true | Greater Portland: Portland, South Portland, Westbrook, Scarborough, Biddeford, Brunswick |
| Maine (other) | ME | false | Lewiston, Augusta, Bangor, Waterville, coastal and rural |
| New Hampshire | NH | false | Lebanon (Dartmouth), Manchester, Nashua, Portsmouth, Concord |
| Vermont | VT | false | Burlington and the rest |
| Boston metro | MA | false | Boston, Cambridge, Route 128 belt, North and South Shore |
| Massachusetts (other) | MA | false | Worcester, Springfield, Cape Cod, Western MA |
| Rhode Island | RI | false | |
| Connecticut | CT | false | |
| NYC metro | NY | false | Five boroughs, Westchester |
| Long Island | NY | false | Nassau, Suffolk |
| Hudson Valley | NY | false | Valhalla, Poughkeepsie, Kingston |
| Upstate NY | NY | false | Albany, Syracuse, Rochester, Buffalo, Binghamton, Utica |
| Jersey Shore | NJ | true | Monmouth and Ocean counties: Asbury Park, Neptune, Long Branch, Brick, Toms River, Red Bank |
| North Jersey | NJ | false | Hackensack, Newark, Paterson, Morristown, New Brunswick |
| South Jersey | NJ | false | Camden, Cherry Hill, Atlantic City, Vineland |
| Philadelphia metro | PA | false | Philadelphia, Main Line, Bucks, Delaware County |
| Pennsylvania (other) | PA | false | Hershey, Lancaster, Allentown, Reading, Scranton, Danville (Geisinger), Pittsburgh |
| Delaware | DE | false | |
| Baltimore metro | MD | false | |
| DC / Northern Virginia | DC, VA, MD | false | Washington, Arlington, Fairfax, Falls Church, Bethesda, Silver Spring |
| Maryland (other) | MD | false | Annapolis, Salisbury, Frederick, Eastern Shore |
| Richmond | VA | false | |
| Hampton Roads | VA | false | Norfolk, Virginia Beach, Newport News, Chesapeake |
| Virginia (other) | VA | false | Charlottesville, Roanoke, Fredericksburg, Lynchburg |
| Triangle | NC | true | Chapel Hill, Durham, Raleigh, Cary, Wake County |
| Charlotte | NC | true | Charlotte, Concord, Huntersville, Lake Norman, Gastonia, Monroe |
| Wilmington / Cape Fear | NC | true | Wilmington, Leland, Southport, Jacksonville, Whiteville |
| Outer Banks / Coastal NC | NC | true | Nags Head, Elizabeth City, Morehead City, New Bern, Greenville, Kinston |
| Triad | NC | false | Winston-Salem, Greensboro, High Point |
| North Carolina (other) | NC | false | Asheville, Fayetteville, Hickory, Pinehurst, Rocky Mount |
| Charleston SC | SC | false | Charleston, Mount Pleasant, North Charleston, Summerville |
| Grand Strand | SC | false | Myrtle Beach, Murrells Inlet, Georgetown, Conway |
| South Carolina (other) | SC | false | Columbia, Greenville, Spartanburg, Florence, Hilton Head, Beaufort |

Hotspot regions: Portland ME, Jersey Shore, Triangle, Charlotte, Wilmington / Cape Fear, Outer Banks / Coastal NC.

## Research agent assignments

| agent | states | output file |
|---|---|---|
| R1 | ME, NH, VT | data/regions/northern_new_england.json |
| R2 | MA, RI, CT | data/regions/southern_new_england.json |
| R3 | NY | data/regions/new_york.json |
| R4 | NJ, PA, DE | data/regions/nj_pa_de.json |
| R5 | MD, DC, VA | data/regions/md_dc_va.json |
| R6 | NC | data/regions/north_carolina.json |
| R7 | SC | data/regions/south_carolina.json |
