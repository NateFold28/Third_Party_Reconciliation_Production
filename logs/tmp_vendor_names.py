from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
select count(*) as c,
       min(file_path) as min_fp,
       max(file_path) as max_fp,
       min(vendor_name) as min_vendor,
       max(vendor_name) as max_vendor
from NETSUITE.DBO.PARSED_VENDOR_DATA
where regexp_substr(file_path, '^[0-9]{4}_[0-9]{2}') >= '2026_01'
""").to_string(index=False))

print(fetch_dataframe("""
select vendor_name, count(*) as c
from NETSUITE.DBO.PARSED_VENDOR_DATA
where regexp_substr(file_path, '^[0-9]{4}_[0-9]{2}') >= '2026_01'
group by 1
order by 2 desc
""").to_string(index=False))
