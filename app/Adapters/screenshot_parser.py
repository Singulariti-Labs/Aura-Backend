from typing import Optional
from dotenv import load_dotenv
import xml.etree.ElementTree as ET
from xml.dom import minidom
import requests
import os

screen_parser_url = os.getenv("SCREEN_PARSING_URL")

async def get_parsed_screen(base64_image: str, screen_width: Optional[int] = 1920, screen_height: Optional[int] = 1080):
    """Function is used to get the content, position and interactivity of elements present on the screen using omni parser server

    input:
        base64_image - screenshot of screen in base64 format
        screen_width - width of the screen set default to 1920
        screen_height - height of the screen set default to 1080

    output:
        parsed content of the screen in XML format

        eg: <screen>
                <element index="0" type="button" interactivity="true">
                    <position>288, 270</position>
                    <content>Submit</content>
                </element>
                <element index="1" type="link" interactivity="false">
                    <position>1056, 567</position>
                    <content>Read more</content>
                </element>
            </screen>    
    """
    try:
        # Step 2: Send the base64 string to the FastAPI server
        url = screen_parser_url
        headers = {"Content-Type": "application/json"}
        payload = {
            "base64_image": base64_image
        }

        response = requests.post(url, json=payload, headers=headers)
        data = response.json()
        screen_elements = data["parsed_content_list"]
        parsed_screen_xml_content = get_parsed_screen_xml(elements=screen_elements, screen_height=screen_height, screen_width=screen_width)
        return parsed_screen_xml_content
    except requests.exceptions.RequestException as e:
        print(f"❌ Screen Parsing API request failed: {e}")
        return {"error": str(e)}
        
def get_parsed_screen_xml(elements: list, screen_width: Optional[int] = 1920, screen_height: Optional[int] = 1080):
    """This function converts parsed UI element data into a structured XML format, making it easier for an LLM to understand screen layout, 
    element positions, interactivity, and content.
    
    input:
        elements (list) - It is the list of the elements present on the screen with there positions, interactivity adn content.
        screen_width - width of the screen set default to 1920
        screen_height - height of the screen set default to 1080
    
    output:
        parsed content of the screen in XML format

        eg: <screen>
                <element index="0" type="button" interactivity="true">
                    <position>288, 270</position>
                    <content>Submit</content>
                </element>
                <element index="1" type="link" interactivity="false">
                    <position>1056, 567</position>
                    <content>Read more</content>
                </element>
            </screen>
    """
    root = ET.Element("screen")

    for index, item in enumerate(elements, start=0):
        element = ET.SubElement(root, "element", {
            "index": str(index),
            "type": item.get("type", "unknown"),
            "interactivity": str(item.get("interactivity", False)).lower()
        })

        # Get bbox and compute center (x, y)
        bbox = item.get("bbox", [])
        if len(bbox) == 4:
            x_min, y_min, x_max, y_max = bbox
            x_center = ((x_min + x_max) / 2.0) * screen_width
            y_center = ((y_min + y_max) / 2.0) * screen_height
            position_str = f"{int(x_center)}, {int(y_center)}"
        else:
            position_str = "0, 0"  # fallback for missing bbox

        ET.SubElement(element, "position").text = position_str

        # Add content
        content = item.get("content", "").strip()
        ET.SubElement(element, "content").text = content

    # Pretty print the XML
    rough_string = ET.tostring(root, encoding='utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")