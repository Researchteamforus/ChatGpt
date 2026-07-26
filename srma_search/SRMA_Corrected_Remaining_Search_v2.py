#!/usr/bin/env python3
"""
Corrected formal rerun for Europe PMC, WHO IRIS and BanglaJOL.
PROSPERO: CRD420261461557

This patch corrects three issues detected in the first parallel run:
1. Europe PMC unfielded searches included full-text/reference mentions.
2. WHO IRIS parsing/searches produced large capped result sets.
3. BanglaJOL queries did not use the exact protocol strings.

WHO GIM/IMSEAR is retained as a documented manual-current-interface route
because the retired hostname does not resolve.
"""
from __future__ import annotations
import csv, hashlib, json, os, re, sys, time, traceback, zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 SRMA-Bangladesh-Corrected-Rerun/2.0"
TIMEOUT = 60
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "application/json,text/html,*/*"})

EPMC_QUERY = (
    'TITLE_ABS:(Bangladesh OR Bangladeshi) AND '
    'TITLE_ABS:(immunization OR immunisation OR vaccination OR vaccine) AND '
    'TITLE_ABS:(child OR children OR infant OR infants OR newborn OR "under five" OR under-five) AND '
    'TITLE_ABS:(coverage OR timeliness OR dropout OR "zero dose" OR zero-dose OR incomplete '
    'OR uptake OR barrier OR determinant OR equity)'
)

IRIS_QUERIES = [
    'Bangladesh AND (immunization OR immunisation OR vaccination) AND (child OR infant)',
    '"Bangladesh" AND "coverage evaluation survey" AND immunization',
    'Bangladesh AND ("zero dose" OR zero-dose OR dropout) AND immunization',
    'Bangladesh AND immunization AND (timeliness OR coverage OR incomplete)',
    'Bangladesh AND EPI AND (child OR infant OR coverage)',
]

BANGLAJOL_QUERIES = [
    "Bangladesh immunization child",
    "Bangladesh vaccination coverage",
    "EPI Bangladesh child",
    "zero-dose Bangladesh",
    "vaccination dropout Bangladesh",
    "vaccination timeliness Bangladesh",
]

COLUMNS = [
    "Source_Record_ID","Source","Search_IDs","Title","Authors","Publication_Year",
    "Publication_Date","Journal_or_Repository","Document_Type","DOI","PMID","PMCID",
    "Abstract","Keywords","Language","Landing_Page_URL","PDF_URL","Source_Internal_ID",
    "Search_Execution_UTC","Raw_Response_File","Notes",
]

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def clean(v):
    return re.sub(r"\s+"," ",str(v or "")).strip()

def doi(v):
    t=clean(v).lower()
    t=re.sub(r"^https?://(dx\.)?doi\.org/","",t)
    t=re.sub(r"^doi:\s*","",t)
    return t.rstrip(".,;)")

def write_csv(path, rows, columns=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    columns=columns or (list(rows[0].keys()) if rows else COLUMNS)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=columns,extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

def save_raw(resp, path):
    path.parent.mkdir(parents=True,exist_ok=True)
    ctype=resp.headers.get("content-type","").lower()
    suffix=".json" if "json" in ctype or resp.text.lstrip().startswith(("{","[")) else ".html"
    target=path.with_suffix(suffix); target.write_bytes(resp.content); return target

def merge(records):
    out=[]; index={}
    for r in records:
        key=doi(r.get("DOI")) or clean(r.get("PMID")) or clean(r.get("Landing_Page_URL")).lower() or clean(r.get("Title")).lower()
        if key not in index:
            index[key]=len(out); out.append(dict(r))
        else:
            kept=out[index[key]]
            ids=set(x for x in clean(kept["Search_IDs"]).split("; ") if x)
            ids.update(x for x in clean(r["Search_IDs"]).split("; ") if x)
            kept["Search_IDs"]="; ".join(sorted(ids))
            for col in COLUMNS:
                if not clean(kept.get(col)) and clean(r.get(col)):
                    kept[col]=r[col]
    return out

def epmc(out):
    endpoint="https://www.ebi.ac.uk/europepmc/webservices/rest/searchPOST"
    cursor="*"; records=[]; page=0; raw=out/"Europe_PMC"/"raw"
    execution=now()
    while cursor:
        page+=1
        resp=SESSION.post(endpoint,data={
            "query":EPMC_QUERY,"resultType":"core","format":"json","pageSize":"1000",
            "cursorMark":cursor,"synonym":"false",
        },timeout=TIMEOUT)
        path=save_raw(resp,raw/f"page_{page:04d}")
        resp.raise_for_status(); data=resp.json()
        results=data.get("resultList",{}).get("result",[])
        for item in results:
            source=clean(item.get("source")); ext=clean(item.get("id"))
            records.append({
                "Source_Record_ID":f"EPMC_{source}_{ext}","Source":"Europe PMC",
                "Search_IDs":"EPMC-CORRECTED-01","Title":clean(item.get("title")),
                "Authors":clean(item.get("authorString")),"Publication_Year":clean(item.get("pubYear")),
                "Publication_Date":clean(item.get("firstPublicationDate")),
                "Journal_or_Repository":clean(item.get("journalTitle")),
                "Document_Type":clean(item.get("pubType")),"DOI":doi(item.get("doi")),
                "PMID":clean(item.get("pmid")),"PMCID":clean(item.get("pmcid")),
                "Abstract":clean(item.get("abstractText")),"Keywords":clean(item.get("keywordList")),
                "Language":clean(item.get("language")),
                "Landing_Page_URL":f"https://europepmc.org/article/{source.lower()}/{ext}" if source and ext else "",
                "PDF_URL":"","Source_Internal_ID":ext,"Search_Execution_UTC":execution,
                "Raw_Response_File":str(path.relative_to(out)),"Notes":"Corrected TITLE_ABS protocol translation",
            })
        nxt=clean(data.get("nextCursorMark"))
        if not results or not nxt or nxt==cursor: break
        cursor=nxt; time.sleep(0.5)
    write_csv(out/"Europe_PMC"/"Europe_PMC_Corrected_Unique.csv",merge(records))
    write_csv(out/"Europe_PMC"/"Europe_PMC_Corrected_Search_Log.csv",[{
        "Search_ID":"EPMC-CORRECTED-01","Exact_Query":EPMC_QUERY,"Execution_UTC":execution,
        "Reported_Hits":len(records),"Unique_Records":len(merge(records)),"Pages":page,
    }])
    return merge(records)

def actual_iris_objects(data):
    try:
        return data["_embedded"]["searchResult"]["_embedded"]["objects"]
    except Exception:
        return []

def unwrap_item(obj):
    if isinstance(obj,dict):
        if "_embedded" in obj and isinstance(obj["_embedded"],dict):
            for key in ["indexableObject","item"]:
                if isinstance(obj["_embedded"].get(key),dict):
                    return obj["_embedded"][key]
        if isinstance(obj.get("indexableObject"),dict):
            return obj["indexableObject"]
    return obj if isinstance(obj,dict) else {}

def meta(metadata, keys):
    if not isinstance(metadata,dict): return ""
    for key in keys:
        vals=metadata.get(key,[])
        if isinstance(vals,list):
            output=[clean(v.get("value")) for v in vals if isinstance(v,dict) and clean(v.get("value"))]
            if output: return "; ".join(output)
    return ""

def iris(out):
    endpoint="https://iris.who.int/server/api/discover/search/objects"
    all_records=[]; logs=[]; raw=out/"WHO_IRIS"/"raw"
    for qn,query in enumerate(IRIS_QUERIES,1):
        sid=f"IRIS-CORRECTED-{qn:02d}"; page=0; execution=now(); records=[]
        while True:
            resp=SESSION.get(endpoint,params={"query":query,"dsoType":"item","page":page,"size":100},timeout=TIMEOUT)
            path=save_raw(resp,raw/sid/f"page_{page+1:04d}")
            resp.raise_for_status(); data=resp.json()
            objects=actual_iris_objects(data)
            if not objects: break
            for wrapped in objects:
                item=unwrap_item(wrapped); md=item.get("metadata",{}) if isinstance(item,dict) else {}
                uuid=clean(item.get("uuid")); title=meta(md,["dc.title"]) or clean(item.get("name"))
                date=meta(md,["dc.date.issued","dc.date"]); ym=re.search(r"\b(18|19|20)\d{2}\b",date)
                handle=meta(md,["dc.identifier.uri"])
                records.append({
                    "Source_Record_ID":f"WHOIRIS_{uuid}","Source":"WHO IRIS","Search_IDs":sid,
                    "Title":title,"Authors":meta(md,["dc.contributor.author","dc.creator"]),
                    "Publication_Year":ym.group(0) if ym else "","Publication_Date":date,
                    "Journal_or_Repository":"WHO IRIS","Document_Type":meta(md,["dc.type"]),
                    "DOI":"","PMID":"","PMCID":"","Abstract":meta(md,["dc.description.abstract","dc.description"]),
                    "Keywords":meta(md,["dc.subject"]),"Language":meta(md,["dc.language.iso","dc.language"]),
                    "Landing_Page_URL":handle or (f"https://iris.who.int/items/{uuid}/full" if uuid else ""),
                    "PDF_URL":"","Source_Internal_ID":uuid,"Search_Execution_UTC":execution,
                    "Raw_Response_File":str(path.relative_to(out)),"Notes":"Exact HAL search-result parser",
                })
            page_info=data.get("_embedded",{}).get("searchResult",{}).get("page",{})
            total_pages=page_info.get("totalPages")
            page+=1
            if total_pages is not None and page>=int(total_pages): break
            time.sleep(0.4)
        logs.append({"Search_ID":sid,"Exact_Query":query,"Execution_UTC":execution,
                     "Rows":len(records),"Pages":page})
        all_records.extend(records)
    unique=merge(all_records)
    write_csv(out/"WHO_IRIS"/"WHO_IRIS_Corrected_Unique.csv",unique)
    write_csv(out/"WHO_IRIS"/"WHO_IRIS_Corrected_Search_Log.csv",logs)
    return unique

def meta_one(soup,names):
    names={n.lower() for n in names}
    for tag in soup.find_all("meta"):
        key=clean(tag.get("name") or tag.get("property")).lower()
        if key in names: return clean(tag.get("content"))
    return ""

def banglajol(out):
    base="https://www.banglajol.info/index.php/index/search/search"
    links={}; logs=[]; raw=out/"BanglaJOL"/"raw"
    for qn,query in enumerate(BANGLAJOL_QUERIES,1):
        sid=f"BANGLAJOL-CORRECTED-{qn:02d}"; execution=now(); found=[]
        for page in range(1,101):
            resp=SESSION.get(base,params={"query":query,"page":page},timeout=TIMEOUT)
            path=save_raw(resp,raw/sid/f"page_{page:04d}"); resp.raise_for_status()
            soup=BeautifulSoup(resp.text,"html.parser")
            page_links=[]
            for a in soup.find_all("a",href=True):
                href=urljoin(resp.url,a["href"]).split("#")[0]
                if re.search(r"/article/view/\d+",href): page_links.append(href)
            page_links=list(dict.fromkeys(page_links))
            new=[x for x in page_links if x not in found]
            if not new: break
            found.extend(new)
            for link in new: links.setdefault(link,set()).add(sid)
        logs.append({"Search_ID":sid,"Exact_Query":query,"Execution_UTC":execution,
                     "Article_Links":len(found)})
    records=[]
    for url,sids in links.items():
        resp=SESSION.get(url,timeout=TIMEOUT); resp.raise_for_status()
        soup=BeautifulSoup(resp.text,"html.parser")
        title=meta_one(soup,["citation_title","dc.title","og:title"])
        authors=[clean(t.get("content")) for t in soup.find_all("meta",attrs={"name":"citation_author"}) if clean(t.get("content"))]
        date=meta_one(soup,["citation_publication_date","dc.date"]); ym=re.search(r"\b(18|19|20)\d{2}\b",date)
        doi_value=meta_one(soup,["citation_doi"]); aid=re.search(r"/article/view/(\d+)",url)
        records.append({
            "Source_Record_ID":f"BANGLAJOL_{aid.group(1) if aid else hashlib.sha1(url.encode()).hexdigest()[:12]}",
            "Source":"BanglaJOL","Search_IDs":"; ".join(sorted(sids)),"Title":title,
            "Authors":"; ".join(authors),"Publication_Year":ym.group(0) if ym else "",
            "Publication_Date":date,"Journal_or_Repository":meta_one(soup,["citation_journal_title","dc.source"]),
            "Document_Type":meta_one(soup,["dc.type"]),"DOI":doi(doi_value),"PMID":"","PMCID":"",
            "Abstract":meta_one(soup,["dc.description","description","og:description"]),
            "Keywords":meta_one(soup,["citation_keywords","dc.subject"]),"Language":meta_one(soup,["dc.language"]),
            "Landing_Page_URL":url,"PDF_URL":meta_one(soup,["citation_pdf_url"]),"Source_Internal_ID":"",
            "Search_Execution_UTC":now(),"Raw_Response_File":"","Notes":"Exact protocol BanglaJOL query",
        })
    unique=merge(records)
    write_csv(out/"BanglaJOL"/"BanglaJOL_Corrected_Unique.csv",unique)
    write_csv(out/"BanglaJOL"/"BanglaJOL_Corrected_Search_Log.csv",logs)
    return unique

def main():
    out=Path.cwd()/f"CORRECTED_REMAINING_SEARCH_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out.mkdir()
    results={}; errors=[]
    for name,func in [("Europe PMC",epmc),("WHO IRIS",iris),("BanglaJOL",banglajol)]:
        try:
            records=func(out); results[name]=len(records); print(name,len(records))
        except Exception as exc:
            errors.append({"Source":name,"Error":str(exc),"Traceback":traceback.format_exc()})
            print(name,"FAILED",exc)
    write_csv(out/"WHO_GIM_IMSEAR_CURRENT_INTERFACE_MANIFEST.csv",[{
        "Source":"WHO Global Index Medicus / IMSEAR",
        "Exact_Query":'Bangladesh AND (immunization OR immunisation OR vaccination) AND (child OR infant) AND (coverage OR timeliness OR dropout OR "zero-dose" OR incomplete)',
        "Current_Interface":"https://www.globalindexmedicus.net/",
        "Status":"Requires documented current-interface execution",
        "Required_Output":"Exact query; execution date; result count; export; screenshot or saved result file",
    }])
    (out/"SUMMARY.json").write_text(json.dumps({"Counts":results,"Errors":errors},indent=2),encoding="utf-8")
    if errors: (out/"ERRORS.json").write_text(json.dumps(errors,indent=2),encoding="utf-8")
    zip_path=out.with_suffix(".zip")
    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as z:
        for p in out.rglob("*"):
            if p.is_file(): z.write(p,p.relative_to(out))
    print("UPLOAD:",zip_path)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
