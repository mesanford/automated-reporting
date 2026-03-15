import pandas as pd
from datetime import datetime, timedelta
import random

def get_mock_data(platform: str):
    """Generates mock performance data for testing API connectors."""
    data = []
    end_date = datetime.now()
    platforms = {
        'google': ['Search - Brand', 'Search - Competitor', 'Performance Max'],
        'meta': ['Awareness - Video', 'Retargeting - Catalog', 'Prospecting - Lookalike'],
        'linkedin': ['Sponsored Content', 'Lead Gen Form', 'InMail Campaign'],
        'tiktok': ['TopView Ads', 'In-Feed Ads', 'Branded Hashtag'],
        'microsoft': ['Bing Search - US', 'Bing Shopping', 'Microsoft Audience Network']
    }
    
    campaigns = platforms.get(platform, ['Generic Campaign A', 'Generic Campaign B'])
    ad_groups = ["Prospecting", "Retargeting", "Brand"]
    ad_assets = ["Video A", "Video B", "Image C", "Carousel D"]
    
    for campaign in campaigns:
        for i in range(14):  # 14 days of data
            date = (end_date - timedelta(days=i)).strftime('%Y-%m-%d')
            spend = random.uniform(50, 500)
            impressions = int(spend * random.uniform(10, 50))
            clicks = int(impressions * random.uniform(0.01, 0.05))
            conversions = int(clicks * random.uniform(0.02, 0.1))
            revenue = conversions * random.uniform(20, 120)
            ad_group = random.choice(ad_groups)
            ad_asset = random.choice(ad_assets)
            
            data.append({
                'date': date,
                'platform': platform,
                'campaign': campaign,
                'ad_group': ad_group,
                'ad_asset': ad_asset,
                'spend': round(spend, 2),
                'impressions': impressions,
                'clicks': clicks,
                'conversions': conversions,
                'revenue': round(revenue, 2),
            })
            
    return pd.DataFrame(data)

import os
import asyncio
from google.ads.googleads.client import GoogleAdsClient

async def fetch_platform_data(platform: str, account_id: str):
    """
    Fetches data from an external API.
    Supports Google Ads Service Account authentication.
    """
    if platform == 'google':
        # Single User Authentication (Desktop App/OAuth2)
        # Note: requires google-ads package
        credentials = {
            "developer_token": os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN"),
            "client_id": os.getenv("GOOGLE_ADS_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_ADS_CLIENT_SECRET"),
            "refresh_token": os.getenv("GOOGLE_ADS_REFRESH_TOKEN"),
            "login_customer_id": os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
            "use_proto_plus": True,
        }
        
        # Placeholder for real API call
        # client = GoogleAdsClient.load_from_dict(credentials)
        pass

    elif platform == 'microsoft':
        # Microsoft Advertising OAuth2 (Desktop/Web flow)
        # Note: typically uses bingads package
        ms_credentials = {
            "client_id": os.getenv("MICROSOFT_CLIENT_ID"),
            "client_secret": os.getenv("MICROSOFT_CLIENT_SECRET"),
            "developer_token": os.getenv("MICROSOFT_DEVELOPER_TOKEN"),
            "refresh_token": os.getenv("MICROSOFT_REFRESH_TOKEN"),
        }
        # Placeholder for real API call
        pass

    # Simulate API latency
    await asyncio.sleep(1.5)
    
    # Generate mock data instead of real API call for now
    return get_mock_data(platform)
