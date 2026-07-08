"""
app/tools/tools.py

All 14 LangChain tools for the Enorsia ecommerce agent.

Tool #1  — search_knowledge_base  : RAG retrieval over faq.json (was a separate path, now just a tool)
Tools #2-14 — ecommerce operations: call the Laravel backend API

Each tool follows the same 4-step pattern:
  1. Sanitise optional inputs (remove LLM-hallucinated null strings).
  2. Build payload — only include fields that are actually present.
  3. Call the backend via post_to_api() (shared error handling).
  4. Return the result as a JSON string for the LLM to reason over.

Tools are grouped by domain:
  Orders      : get_order_status, cancel_order, get_order_details,
                check_order_incident, send_order_invoice
  Customer    : get_shipping_address, update_shipping_address
  Returns     : create_return_request, get_return_status
  Exchanges   : get_order_for_exchange, submit_exchange_request
  Store ops   : validate_discount_code, create_support_ticket
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, List, get_args

from dotenv import load_dotenv
from langchain_core.tools import tool, ToolException
from pydantic import EmailStr
from functools import wraps

from app.models import (
    CancelOrderInput,
    CreateReturnInput,
    CreateSupportTicketInput,
    IncidentOrderInput,
    KnowledgeBaseInput,
    GetOrderForExchangeInput,
    OrderAndEmailInput,
    OrderLookupInput,
    ReturnItem,
    ReturnStatusInput,
    SendInvoiceInput,
    SubmitExchangeInput,
    SupportTicketCategory,
    UpdateShippingInput,
    ValidateDiscountInput,
    ProductSearchInput,
    ProductListInput,
)
from app.utils.utils import error_response, post_to_api, sanitize_optional_str, CREATE_SUPPORT_TICKET_DOC
from app.config import get_settings
from collections import defaultdict


settings = get_settings()
load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared config — resolved once at import time
# ---------------------------------------------------------------------------

_BASE_URL: str = os.getenv("ENOX_API_URL", "")
_HEADERS: dict = {
    "X-INTERNAL-KEY": os.getenv("ENOX_API_KEY", ""),
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def _api(endpoint: str, payload: dict) -> dict:
    """Thin wrapper so individual tools stay free of boilerplate."""
    return post_to_api(endpoint, payload, _HEADERS, logger, base_url=_BASE_URL)


def _to_json(data: dict) -> str:
    """Serialise the backend response to a compact JSON string for the LLM."""
    return json.dumps(data, ensure_ascii=False)


# ===========================================================================
# Tool 1 — Knowledge base search (RAG)
# ===========================================================================

@tool("search_knowledge_base", args_schema=KnowledgeBaseInput)
def search_knowledge_base(
    query: str,
    category_filter: Optional[str] = None,
) -> str:
    """
    Search the Enorsia FAQ knowledge base to answer general questions about
    store policy, shipping, returns, payments, accounts, and product information.

    Use this tool when the customer asks a general question that does not
    require looking up a specific live order — for example:
      - "What is your return policy?"
      - "How long does delivery take?"
      - "Do you offer cash on delivery?"
      - "Can I change my order after placing it?"

    Returns a JSON string with the most relevant FAQ answers found.
    If the knowledge base has no relevant answer, the result will say so.
    """
    # Import here to avoid circular imports at module load time.
    # rag_engine is a singleton — this import is essentially free after startup.
    from app.rag.engine import rag_engine

    if not rag_engine.is_ready:
        return _to_json(error_response(
            "Knowledge base is not available right now."
        ))

    docs = rag_engine.retrieve(query=query, category_filter=category_filter)

    if not docs:
        return _to_json(error_response(
            "No relevant information found in the knowledge base."
        ))

    results = []
    for doc in docs:
        results.append({
            "question": doc.metadata.get("question", ""),
            "answer":   doc.metadata.get("answer", ""),
            "category": doc.metadata.get("category", ""),
            "action_type": doc.metadata.get("action_type", ""),
        })

    return _to_json({"status": True, "message": "Knowledge base results", "data": results})

# ===========================================================================
# Tool — Product search (RAG over product catalogue)
# ===========================================================================

@tool("search_products", args_schema=ProductSearchInput)
def search_products(
    query: str,
    department: str,
    category: Optional[str] = None,
    color: Optional[str] = None,
    size: Optional[str] = None,
    occasion: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    in_stock_only: bool = True,
    top_k: int = 5,
) -> str:
    """
    Search the Enorsia product catalogue for items matching the customer's request.

    Use this tool whenever the customer wants to find, browse, or discover products.

    Examples:
    - "Show me black bodysuits"
    - "Do you have red summer dresses under £40?"
    - "I need something for a party in size 12"
    - "What activewear do you sell?"
    - "Looking for a sleeveless top for holiday"

    Important:
    - The `department` is required and must be one of: `women`, `men`, `girls`, or `boys`.
    - If the customer's request does not clearly indicate the department, do **not** guess. Ask a clarifying question first, such as:
    - "Are you looking for women's, men's, girls', or boys' clothing?"
    - Once the department is known, use it when calling this tool.
    - Apply any other filters mentioned by the customer, such as category, colour, size, occasion, price range, or stock availability.

    This tool performs a semantic + keyword hybrid search and returns a JSON list of matching products with full details, including name, price, colours, sizes, images, and product URL.
    """
    logger.info("PRODUCT-SEARCH | search_products() called with query=%s and filters=%s", query, {
        k: v for k, v in {
            "department": department,
            "category": category,
            "color": color,
            "size": size,
            "occasion": occasion,
            "min_price": min_price,
            "max_price": max_price,
            "in_stock_only": in_stock_only,
        }.items() if v is not None
    })
    from app.rag.product_engine import product_rag_engine

    if not product_rag_engine.is_ready:
        logger.info("PRODUCT-SEARCH | Product catalogue is not available right now product_rag_engine not ready.")
        return _to_json({
            "status": False,
            "message": "Product catalogue is not available right now. Please try again later.",
            "products": []
        })

    filters_applied = {
        k: v for k, v in {
            "department": department,
            "category": category,
            "color": color,
            "size": size,
            "occasion": occasion,
            "min_price": min_price,
            "max_price": max_price,
            "in_stock_only": in_stock_only,
        }.items() if v is not None and v is not False
    }

    products = product_rag_engine.retrieve(
        query=query,
        department=department,
        category=category,
        color=color,
        size=size,
        occasion=occasion,
        min_price=min_price,
        max_price=max_price,
        in_stock_only=in_stock_only,
        top_k=top_k,
    )

    if not products:
        logger.info("PRODUCT-SEARCH | No products found matching query=%s and filters=%s", query, filters_applied)
        return _to_json({
            "status": False,
            "message": (
                "No products found matching your search. "
                "Try broadening the query or removing some filters."
            ),
            "query_understood": query,
            "filters_applied": filters_applied,
            "products": []
        })

    # ── Slim down the payload — only what the LLM needs ───────────────────
    slim_products = []
    for p in products:
        attrs = p.get("attributes", {})
        slim_products.append({
            "product_id":      p["product_id"],
            "product_name":    p["product_name"],
            "product_url":     p["product_url"],
            "product_image":   p["product_image"],
            "category":        p.get("category"),
            "department":      p.get("department"),
            "price":           p.get("price"),
            "currency":        p.get("currency", "GBP"),
            "discount_price":  p.get("discount_price"),
            "discount_percent": p.get("discount_percent"),
            "has_discount":    p.get("has_discount", False),
            "in_stock":        p.get("in_stock", True),
            "rating":          p.get("rating"),
            "total_reviews":   p.get("total_reviews"),
            "colors":          attrs.get("colors", []),
            "sizes":           attrs.get("sizes", []),
            "fabric":          attrs.get("fabric"),
            "fit":             attrs.get("fit"),
            "sleeve":          attrs.get("sleeve"),
            "season":          attrs.get("season"),
            "occasion":        attrs.get("occasion", []),
            "neckline":        attrs.get("neckline"),
        })

    json_response = _to_json({
        "status": True,
        "message": f"Found {len(slim_products)} product(s) matching your search.",
        "query_understood": query,
        "filters_applied": filters_applied,
        "products": slim_products,
    })

    logger.info("PRODUCT-SEARCH | search_products() returned %s", json_response)

    return json_response


@tool("what_does_enorsia_sale")
def what_does_enorsia_sale() -> dict:
    """
    Returns a full list of Enorsia's departments, categories and all website usefull links.
    Used for catalog queries such as:
    - What does Enorsia sell?
    - Show me all women categories
    - Do you sell jeans for boys?
    - What categories are available?
    - enorsia privacy, policy, login, register, usefull website url.

    Returns:
        dict: Structured catalog data.
        Note: Always return data with urls
    """
    return {
        "summary": "Enorsia offers clothing and fashion products for Men, Women, Girls, and Boys across a variety of categories.",
        "departments": {
            "Women": {
                "url": "https://enorsia.com/women",
                "categories": [
                    {"name": "Co-ords and multipacks", "url": "https://enorsia.com/c/women/co-order-multipack"},
                    {"name": "Coats and Jackets", "url": "https://enorsia.com/c/women/coats-jackets-1"},
                    {"name": "Dresses", "url": "https://enorsia.com/c/women/dresses"},
                    {"name": "Hoodies and Sweatshirts", "url": "https://enorsia.com/c/women/hoodies-sweatshirts"},
                    {"name": "Jeans", "url": "https://enorsia.com/c/women/jeans-1"},
                    {"name": "Jumpsuits and Playsuits", "url": "https://enorsia.com/c/women/jumpsuits"},
                    {"name": "Leggings and Joggers", "url": "https://enorsia.com/c/women/leggings-joggers"},
                    {"name": "Loungewear", "url": "https://enorsia.com/c/women/loungwear"},
                    {"name": "Nightwear", "url": "https://enorsia.com/c/women/nightwear"},
                    {"name": "Polo Shirts", "url": "https://enorsia.com/c/women/polo-shirts"},
                    {"name": "Shirts and Blouses", "url": "https://enorsia.com/c/women/blouse-shirts"},
                    {"name": "Shorts", "url": "https://enorsia.com/c/women/shorts"},
                    {"name": "Skirts", "url": "https://enorsia.com/c/women/skirts"},
                    {"name": "Tops and T-Shirts", "url": "https://enorsia.com/c/women/tops-t-shirts"},
                    {"name": "Trousers", "url": "https://enorsia.com/c/women/trousers"}
                ]
            },
            "Men": {
                "url": "https://enorsia.com/men",
                "categories": [
                    {"name": "Chinos", "url": "https://enorsia.com/c/men/chinos-trousers"},
                    {"name": "Co-ords and Multipacks", "url": "https://enorsia.com/c/men/co-order-multipack"},
                    {"name": "Coats and Jackets", "url": "https://enorsia.com/c/men/coats-jackets"},
                    {"name": "Jeans", "url": "https://enorsia.com/c/men/jeans"},
                    {"name": "Joggers", "url": "https://enorsia.com/c/men/joggers"},
                    {"name": "Loungewear", "url": "https://enorsia.com/c/men/loungewear"},
                    {"name": "Nightwear", "url": "https://enorsia.com/c/men/nightwear-1"},
                    {"name": "Polo Shirts", "url": "https://enorsia.com/c/men/polo-shirts-1"},
                    {"name": "Shirts", "url": "https://enorsia.com/c/men/shirts"},
                    {"name": "Shorts", "url": "https://enorsia.com/c/men/shorts-1"},
                    {"name": "Sweatshirts and Hoodies", "url": "https://enorsia.com/c/men/sweatshirts-hoodies"},
                    {"name": "T-Shirts and Vests", "url": "https://enorsia.com/c/men/t-shirts-vest"},
                    {"name": "Trousers", "url": "https://enorsia.com/c/men/trousers-3"}
                ]
            },
            "Girls": {
                "url": "https://enorsia.com/girls",
                "categories": [
                    {"name": "Co-ords and Multipacks", "url": "https://enorsia.com/c/girls/co-ords-multipacks-1"},
                    {"name": "Dresses and Jumpsuits", "url": "https://enorsia.com/c/girls/dresses-jumpsuits-1"},
                    {"name": "Jeans and Dungarees", "url": "https://enorsia.com/c/girls/jeans-dungarees"},
                    {"name": "Leggings and Joggers", "url": "https://enorsia.com/c/girls/legging-joggers"},
                    {"name": "Nightwear", "url": "https://enorsia.com/c/girls/nightwear-2"},
                    {"name": "Shorts", "url": "https://enorsia.com/c/girls/shorts-2"},
                    {"name": "Sweatshirts & Hoodies", "url": "https://enorsia.com/c/girls/sweatshirts-hoodies-1"},
                    {"name": "Tops and T-Shirts", "url": "https://enorsia.com/c/girls/tops-t-shirts-1"},
                    {"name": "Trousers", "url": "https://enorsia.com/c/girls/trousers-1"}
                ]
            },
            "Boys": {
                "url": "https://enorsia.com/boys",
                "categories": [
                    {"name": "Co-ords and Multipacks", "url": "https://enorsia.com/c/boy/co-ords-multipacks-2"},
                    {"name": "Coats and Jackets", "url": "https://enorsia.com/c/boy/coats-jackets-3"},
                    {"name": "Jeans", "url": "https://enorsia.com/c/boy/jeans-3"},
                    {"name": "Joggers", "url": "https://enorsia.com/c/boy/joggers-1"},
                    {"name": "Nightwear", "url": "https://enorsia.com/c/boy/nightwear-3"},
                    {"name": "Shirts", "url": "https://enorsia.com/c/boy/shirts-1"},
                    {"name": "Shorts", "url": "https://enorsia.com/c/boy/shorts-3"},
                    {"name": "Sweatshirts and Hoodies", "url": "https://enorsia.com/c/boy/sweatshirts-hoodies-2"},
                    {"name": "T-Shirts and Vests", "url": "https://enorsia.com/c/boy/t-shirts-vest-1"},
                    {"name": "Trousers", "url": "https://enorsia.com/c/boy/trousers-2"}
                ]
            },
            "Christmas": {
                "url": "https://enorsia.com/christmas-shop",
            },
            "Sale": {
                "url": "https://enorsia.com/clear-sale",
                "note": "sale product up to 70% off"
            },
            "website_usefull_links": [
                {
                    "name": "enorsia.com",
                    "url": "https://enorsia.com/",
                },
                {
                    "name": "Help Center",
                    "url": "https://enorsia.com/help-center",
                },
                {
                    "name":"contact us",
                    "url":"https://enorsia.com/contact"
                },
                {
                    "name":"About Enorsia",
                    "url":"https://enorsia.com/about"
                },
                {
                    "name":"Terms & Conditions",
                    "url":"https://enorsia.com/terms-conditions"
                },
                {
                    "name":"Privacy Policy",
                    "url":"https://enorsia.com/privacy-policy"
                },
                {
                    "name":"Cookies Policy",
                    "url":"https://enorsia.com/cookies"
                },
                {
                    "name":"Order Tracking",
                    "url":"https://enorsia.com/order/tracking"
                },
                {
                    "name":"Student Discount",
                    "url":"https://enorsia.com/student-discount"
                },
                {
                    "name":"Delivery Information",
                    "url":"https://enorsia.com/delivery"
                },
                {
                    "name":"Return Information",
                    "url":"https://enorsia.com/return-policy"
                },
                {
                    "name":"Return and Exchange Request",
                    "url":"https://enorsia.com/return-order"
                },
                {
                    "name":"Enorsia Blog",
                    "url":"https://enorsia.com/enorsia-edit"
                },
                {
                    "name":"Login",
                    "url":"https://enorsia.com/customer/login"
                },
                {
                    "name":"Register",
                    "url":"https://enorsia.com/customer/register"
                }
            ],
        }
    }


@tool("product_title_list", args_schema=ProductListInput)
def product_title_list(department: str) -> str:
    """
    Return the titles of all products in the specified department.

    Args:
        department: Filter products by department. Accepted values are:
            - Women
            - Men
            - Girls
            - Boys

    Returns:
        A newline-separated list of all product titles in the selected department.
    """

    json_path = settings.product_data_path

    with open(json_path, "r", encoding="utf-8") as f:
        products = json.load(f)

    # Group product titles by department
    grouped = defaultdict(list)

    for product in products:
        dept = product.get("department", "").strip().lower()
        title = product.get("product_name", "").strip()

        if title:
            grouped[dept].append(title)

    dept = department.strip().lower()

    if dept not in grouped:
        return f"No products found for department '{department}'."

    return "\n".join(grouped[dept])
    

# ===========================================================================
# ORDER DOMAIN
# ===========================================================================

@tool("get_order_status", args_schema=OrderLookupInput)
def get_order_status(
    order_id: int
) -> str:
    """
    Retrieve live fulfillment and tracking status for an order.

    order_id must be provided.
    Returns a JSON string with status, message, and data.
    """

    logger.info("ORDER-STATUS | get_order_status() called with order_id=%s", order_id)

    order_id = order_id
    payload = {}
    payload["order_id"] = order_id

    logger.info("ORDER-STATUS | payload=%s", payload)
    return _to_json(_api("/api/order/status", payload))


@tool("cancel_order", args_schema=CancelOrderInput)
def cancel_order(order_id: int, email: EmailStr, cancel_reason: str) -> str:
    """
    Cancel an order. Both order_id, email and cancel_reason are required for verification.
    Returns a JSON string confirming success or explaining the failure.
    """
    payload = {"order_id": order_id, "email": email.strip(), "cancel_reason": cancel_reason.strip()}
    logger.info("ORDER-CANCEL | cancel_order() called with payload=%s", payload)
    return _to_json(_api("/api/order/cancel", payload))


@tool("get_order_details", args_schema=OrderAndEmailInput)
def get_order_details(order_id: int, email: EmailStr) -> str:
    """
    Fetch full order details — line items, pricing, and fulfilment info.
    Both order_id and email are required.
    Returns a JSON string with the complete order record.
    """
    logger.info("ORDER-DETAILS | get_order_details called with order_id=%s, email=%s", order_id, email)
    payload = {"order_id": order_id, "email": email.strip()}
    logger.info("ORDER-DETAILS | payload=%s", payload)
    return _to_json(_api("/api/orders", payload))


@tool("check_order_incident", args_schema=IncidentOrderInput)
def check_order_incident(
    email: EmailStr,
) -> str:
    """
    Check whether an order has any open incidents or confirmation issues.

    Use this when a customer reports a problem with their order (e.g. order not placed, payment confirmaed but not order placed, payment dispute). email required.
    Returns a JSON string with incident details.
    """
    payload = { "email": email.strip() }
    logger.info("ORDER-INCIDENT | payload=%s", payload)
    return _to_json(_api("/api/order/confirmation", payload))


@tool("send_order_invoice", args_schema=SendInvoiceInput)
def send_order_invoice(order_id: int) -> str:
    """
    Trigger the backend to email the invoice for the specified order.
    Returns a JSON string confirming the email was dispatched.
    """
    payload = {"order_id": order_id}
    logger.info("SEND-INVOICE | send_order_invoice() called with payload=%s", payload)
    return _to_json(_api("/api/order/invoice/send", payload))


# ===========================================================================
# CUSTOMER DOMAIN
# ===========================================================================

@tool("get_shipping_address", args_schema=OrderAndEmailInput)
def get_shipping_address(order_id: int, email: EmailStr) -> str:
    """
    Retrieve the shipping address currently on file for an order.
    Both order_id and email are required for verification.
    Returns a JSON string with the full shipping address record.
    """
    payload = {"order_id": order_id, "email": email.strip()}
    logger.info("SHIPPING-ADDRESS | get_shipping_address() called with payload=%s", payload)
    return _to_json(_api("/api/shipping-address", payload))


@tool("update_shipping_address", args_schema=UpdateShippingInput)
def update_shipping_address(
    order_id: int,
    email: EmailStr,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    phone: Optional[str] = None,
    address_line_1: Optional[str] = None,
    address_line_2: Optional[str] = None,
    city: Optional[str] = None,
    post_code: Optional[str] = None,
) -> str:
    """
    Update one or more fields on the shipping address for an order, email will not changed.

    Only supply the fields that the customer wants to change — unchanged
    fields should be omitted (or passed as None). order_id and email are
    always required. Returns a JSON string confirming the update.
    """
    # Required fields
    payload: dict = {
        "order_id": order_id,
        "email": email.strip(),
    }

    # Optional fields — only include if the customer actually provided them
    optional_fields = {
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "address_line_1": address_line_1,
        "address_line_2": address_line_2,
        "city": city,
        "post_code": post_code,
    }
    for field, value in optional_fields.items():
        cleaned = sanitize_optional_str(value)
        if cleaned:
            payload[field] = cleaned

    logger.info("SHIPPING-ADDRESS | update_shipping_address() called with payload=%s", payload)
    return _to_json(_api("/api/shipping-address/update", payload))


# ===========================================================================
# RETURNS DOMAIN
# ===========================================================================

@tool("create_return_request", args_schema=CreateReturnInput)
def create_return_request(
    order_id: int,
    email: EmailStr,
    items: List[ReturnItem],
) -> str:
    """
    Submit a customer return request. 

    PREREQUISITES:
    - You MUST call `get_order_details` first to view the items in the order.
    - You MUST explicitly show the order's items to the user and ask them which specific items they want to return, the quantity, and the specific reason for each.
    - Do NOT guess or hallucinate the item IDs, quantities, or reasons.

    ARGUMENTS MAPPING:
    - Each item in the `items` list must map `ordered_product_unique_id` to the integer value found under `data.order_products.*.ordered_product_unique_id` from the `get_order_details` response.
    - `quantity` must be a positive integer matching or less than what was originally ordered.
    - `return_reasons` must be the exact reason provided by the user.

    Returns:
        str: A JSON string containing the successfully created return request details or an error message.
    """
    serialized_items = [item.model_dump() for item in items]
    payload = {
        "order_id": order_id,
        "email": email.strip(),
        "items": serialized_items,
    }
    logger.info("CREATE-RETURN | create_return_request() called with payload=%s", payload)
    return _to_json(_api("/api/order/return", payload))


@tool("check_order_return_request_status", args_schema=ReturnStatusInput)
def check_order_return_request_status(return_request_id: str) -> str:
    """
    Check the current shipment status of a previously submitted return request.

    Use the return_request_id returned when the return was first created.
    Returns a JSON string with carrier tracking and return status details.

    Note: The backend field name is 'return_reqest_id' (legacy typo preserved).
    """
    payload = {"return_reqest_id": return_request_id}
    logger.info("RETURN-STATUS | check_order_return_request_status() called with payload=%s", payload)
    return _to_json(_api("/api/order/return/status", payload))


# ===========================================================================
# EXCHANGES DOMAIN
# ===========================================================================

@tool("get_order_for_exchange", args_schema=GetOrderForExchangeInput)
def get_order_for_exchange(order_number: int) -> str:
    """
    Fetch the order data needed to build an exchange request — eligible items,
    available sizes, and colours.

    Call this first; then pass the result's product details into
    submit_exchange_request. Returns a JSON string with exchangeable items.
    """
    payload = {"order_number": order_number}
    return _to_json(_api("/api/order/exchange", payload))


@tool("submit_exchange_request", args_schema=SubmitExchangeInput)
def submit_exchange_request(
    order_number: str,
    exchangeable_items: list,
    comment: Optional[str] = None,
) -> str:
    """
    Submit an exchange request for one or more items in an order.

    Each item in exchangeable_items must include:
      - product_id (int)
      - size_name (str)
      - color_name (str)
      - quantity (int, minimum 1)
      - reason (str, optional)

    Returns a JSON string confirming the exchange request was received.
    """
    payload: dict = {
        "order_number": order_number.strip(),
        "exchangeable_items": exchangeable_items,
    }

    comment_cleaned = sanitize_optional_str(comment)
    if comment_cleaned:
        payload["comment"] = comment_cleaned

    return _to_json(_api("/api/order/exchange/store", payload))


# ===========================================================================
# STORE OPS DOMAIN
# ===========================================================================

@tool("validate_discount_code", args_schema=ValidateDiscountInput)
def validate_discount_code(coupon_code: str) -> str:
    """
    Check whether a coupon or discount code is valid and retrieve its details
    (discount percentage, expiry date, applicable products, etc.).

    Returns a JSON string with validity status and discount information.
    """
    payload = {"coupon_code": coupon_code.strip()}
    logger.info("VALIDATE-DISCOUNT | validate_discount_code() called with payload=%s", payload)
    return _to_json(_api("/api/coupon/checker", payload))

@tool("create_support_ticket", args_schema=CreateSupportTicketInput, description=CREATE_SUPPORT_TICKET_DOC)
def create_support_ticket(
    name: str,
    email: str,
    phone: str,
    category: SupportTicketCategory,
    subject: str,
    message: str,
    order_id: Optional[str] = None,
) -> str:
    payload: dict = {
        "name": name.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
        "category": category.strip(),
        "subject": subject.strip(),
        "message": message.strip(),
    }

    order_id_cleaned = sanitize_optional_str(order_id)
    if order_id_cleaned:
        payload["order_id"] = order_id_cleaned
    logger.info("CREATE-SUPPORT-TICKET | create_support_ticket() called with payload=%s", payload)
    return _to_json(_api("/api/general/inquiry/create", payload))


# ===========================================================================
# Complete tool registry — imported by graph.py
# ===========================================================================

ALL_TOOLS = [
    search_knowledge_base,   # Tool 1  — RAG (knowledge base)
    get_order_status,        # Tool 2  — Orders
    cancel_order,            # Tool 3
    get_order_details,       # Tool 4
    check_order_incident,    # Tool 5
    send_order_invoice,      # Tool 6
    get_shipping_address,    # Tool 7  — Shipping
    update_shipping_address, # Tool 8
    create_return_request,   # Tool 9  — Returns
    check_order_return_request_status,       # Tool 10
    #get_order_for_exchange,  # Tool 11 — Exchanges
    #submit_exchange_request, # Tool 12
    validate_discount_code,  # Tool 13 — Store ops
    create_support_ticket,   # Tool 14
    what_does_enorsia_sale,   # Tool 15
    search_products,            # Tool 16 — RAG (product catalogue)
    product_title_list,         # Tool 17
]