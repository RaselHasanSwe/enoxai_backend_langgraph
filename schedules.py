import logging

from app.rag.product_image_sync import sync_product_images
from app.rag.product_image_engine import product_image_engine

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    # result = sync_product_images()

    # print(
    #     f"Done. Added={result['added']},"
    #     f"Changed={result['changed']}, "
    #     f"Total={result['total']}"
    # )

    product_image_engine.build_index()