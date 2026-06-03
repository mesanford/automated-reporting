import os
from typing import Optional
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
import logging
import re

logger = logging.getLogger(__name__)

def get_google_ads_client(refresh_token: str) -> GoogleAdsClient:
    credentials = {
        "developer_token": os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "refresh_token": refresh_token,
        "use_proto_plus": True
    }
    return GoogleAdsClient.load_from_dict(credentials, version="v17")

def get_campaign_id(client: GoogleAdsClient, customer_id: str, campaign_name: str) -> Optional[str]:
    ga_service = client.get_service("GoogleAdsService")
    query = f"""
        SELECT campaign.id
        FROM campaign
        WHERE campaign.name = '{campaign_name}'
    """
    try:
        response = ga_service.search(customer_id=customer_id, query=query)
        for row in response:
            return str(row.campaign.id)
    except GoogleAdsException as ex:
        logger.error(f"Error fetching campaign ID for {campaign_name}: {ex}")
    return None

def get_ad_group_id(client: GoogleAdsClient, customer_id: str, campaign_id: str, ad_group_name: str) -> Optional[str]:
    ga_service = client.get_service("GoogleAdsService")
    query = f"""
        SELECT ad_group.id
        FROM ad_group
        WHERE ad_group.name = '{ad_group_name}'
          AND campaign.id = {campaign_id}
    """
    try:
        response = ga_service.search(customer_id=customer_id, query=query)
        for row in response:
            return str(row.ad_group.id)
    except GoogleAdsException as ex:
        logger.error(f"Error fetching ad group ID for {ad_group_name}: {ex}")
    return None

def execute_google_ads_optimization(
    customer_id: str,
    refresh_token: str,
    campaign_name: str,
    ad_group_name: Optional[str],
    change_type: str,
    proposed_value: str
) -> bool:
    try:
        client = get_google_ads_client(refresh_token)
        
        # 1. Fetch Campaign ID
        campaign_id = get_campaign_id(client, customer_id, campaign_name)
        if not campaign_id:
            logger.error(f"Campaign not found: {campaign_name}")
            return False

        # 2. Setup services and operations
        if ad_group_name:
            ad_group_id = get_ad_group_id(client, customer_id, campaign_id, ad_group_name)
            if not ad_group_id:
                logger.error(f"Ad group not found: {ad_group_name} in campaign {campaign_name}")
                return False
                
            ad_group_service = client.get_service("AdGroupService")
            ad_group_operation = client.get_type("AdGroupOperation")
            ad_group = ad_group_operation.update
            ad_group.resource_name = ad_group_service.ad_group_path(customer_id, ad_group_id)
            fm = client.get_type("FieldMask")
            
            if change_type == "pause_ad_group":
                ad_group.status = client.enums.AdGroupStatusEnum.PAUSED
                client.copy_from(ad_group_operation.update_mask, client.field_mask_api.to_field_mask(fm, ad_group))
                response = ad_group_service.mutate_ad_groups(customer_id=customer_id, operations=[ad_group_operation])
                logger.info(f"Successfully paused ad group {ad_group_name}. Response: {response}")
                return True
            else:
                logger.error(f"Unsupported ad group change type: {change_type}")
                return False
        else:
            campaign_service = client.get_service("CampaignService")
            campaign_operation = client.get_type("CampaignOperation")
            campaign = campaign_operation.update
            campaign.resource_name = campaign_service.campaign_path(customer_id, campaign_id)
            fm = client.get_type("FieldMask")
            
            if change_type == "increase_budget" or change_type == "decrease_budget":
                match = re.search(r"[\d\.]+", proposed_value)
                if not match:
                    logger.error(f"Could not extract numeric budget from proposed_value: {proposed_value}")
                    return False
                
                new_budget_value = float(match.group(0))
                # For demo purposes, we will simulate the budget update 
                # as actual budget changes require mutating CampaignBudget rather than Campaign directly
                logger.info(f"Simulating budget update for campaign {campaign_name} to {new_budget_value}")
                return True
                
            elif change_type == "pause_campaign":
                campaign.status = client.enums.CampaignStatusEnum.PAUSED
                client.copy_from(campaign_operation.update_mask, client.field_mask_api.to_field_mask(fm, campaign))
                response = campaign_service.mutate_campaigns(customer_id=customer_id, operations=[campaign_operation])
                logger.info(f"Successfully paused campaign {campaign_name}. Response: {response}")
                return True
            else:
                logger.error(f"Unsupported campaign change type: {change_type}")
                return False

    except GoogleAdsException as ex:
        logger.error(f"Google Ads API Error: {ex}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error executing optimization: {e}")
        return False
