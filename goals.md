# POC
- Does TB have all the content cached that we want
- Can we scrape https://fitgirl-repacks.site/ and give the option to cache uncached links

# Test download with a title
- I will give you a title to test with, we'll note if it was cached, need to be pre-cached or anything else

# Auto install option
- Can we detect a pattern for installer exes such that we can install games in one click
- Can we do that in linux - this is a first party platform, windows support will come naturally alongside it


## Architecture in order

### Python BE
1. Scraper 
    - Looks in TB for cache
    - No cache? scrape fitgirl-repacks for a download and cache in TB 

2. Downloader
    - Downloader which pull from TB 

3. Installer - needs POC
    - Auto installer with linux as first class
    - Installs should install to steam library
    
### Flutter FE
1. Graphical interface wrapping python back end
