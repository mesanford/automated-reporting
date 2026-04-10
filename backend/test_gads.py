import asyncio
import os
from dotenv import load_dotenv
from google.ads.googleads.client import GoogleAdsClient

load_dotenv()

developer_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")
client_id = os.getenv("GOOGLE_ADS_CLIENT_ID")
client_secret = os.getenv("GOOGLE_ADS_CLIENT_SECRET")
final_refresh_token = os.getenv("GOOGLE_ADS_REFRESH_TOKEN").strip()

credentials = {
    "developer_token": developer_token,
    "client_id": client_id,
    "client_secret": client_secret,
    "refresh_token": final_refresh_token,
    "use_proto_plus": True,
}

# The user is probably querying against a specific customer ID from the DB in reality, 
# but we can try to pull all accessible customers or assume a specific one if it's in the env.
login_customer_id = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip()
if login_customer_id:
    credentials["login_customer_id"] = login_customer_id.replace("-", "")

def run_query(query_type, query_str):
    client = GoogleAdsClient.load_from_dict(credentials)
    service = client.get_service("GoogleAdsService")
    
    # We need to find the customer ID. Let's list accessible customers.
    customer_service = client.get_service("CustomerService")
    accessible_customers = customer_service.list_accessible_customers()
    if not accessible_customers.resource_names:
        print("No accessible customers.")
        return
        
    customer_resource = accessible_customers.resource_names[0]
    customer_id = customer_resource.split("/")[-1]
    
    print(f"Executing {query_type} query against customer {customer_id}...")
    try:
        results = service.search_stream(customer_id=customer_id, query=query_str)
        total_cost = 0
        total_conv_value = 0
        total_conv = 0
        for batch in results:
            for row in batch.results:
                total_cost += float(row.metrics.cost_micros or 0) / 1_000_000
                total_conv_value += float(row.metrics.conversions_value or 0)
                total_conv += float(row.metrics.conversions or 0)
        print(f"Results for {query_type}:")
        print(f"Cost: {total_cost}, Conv. Value: {total_conv_value}, Conversions: {total_conv}")
    except Exception as e:
        print(f"Error querying: {e}")

if __name__ == "__main__":
    start, end = "2026-02-01", "2026-03-31"
    q_ad = f"""
    SELECT
      metrics.cost_micros,
      metrics.conversions,
      metrics.conversions_value
    FROM ad_group_ad
    WHERE segments.date BETWEEN '{start}' AND '{end}'
    """
    
    q_camp = f"""
    SELECT
      metrics.cost_micros,
      metrics.conversions,
      metrics.conversions_value
    FROM campaign
    WHERE segments.date BETWEEN '{start}' AND '{end}'
    """
    run_query("ad_group_ad", q_ad)
    print("-" * 20)
    run_query("campaign", q_camp)

