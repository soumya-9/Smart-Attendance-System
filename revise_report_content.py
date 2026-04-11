import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

SOURCE_DOC = Path(r"C:\Users\soumy\Downloads\SMART ATTENDANCE SYSTEM - formatted.docx")
OUTPUT_DOC = Path(r"C:\Users\soumy\Downloads\SMART ATTENDANCE SYSTEM - final.docx")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W_NS, "r": R_NS}

ET.register_namespace("w", W_NS)
ET.register_namespace("r", R_NS)
ET.register_namespace("xml", XML_NS)


def qn(name: str) -> str:
    prefix, tag = name.split(":")
    return f"{{{NS[prefix]}}}{tag}"


def replace_paragraph_text(paragraph, new_text: str):
    ppr = paragraph.find("w:pPr", NS)
    for child in list(paragraph):
        if child is not ppr:
            paragraph.remove(child)
    run = ET.SubElement(paragraph, qn("w:r"))
    text_node = ET.SubElement(run, qn("w:t"))
    if new_text.startswith(" ") or new_text.endswith(" "):
        text_node.set(f"{{{XML_NS}}}space", "preserve")
    text_node.text = new_text


REPLACEMENTS = {}

REPLACEMENTS.update(
    {
        46: "I would like to express my sincere gratitude to Prof. Debajyoti Chatterjee for his guidance, valuable feedback, and continuous encouragement throughout this project. I am also thankful to the Department of Computer Science and Engineering, University of Engineering and Management, Jaipur, for providing the academic environment and resources required to complete this work. Finally, I am grateful to my family and friends for their support and motivation during the development, testing, and documentation of the project.",
        50: "This project presents a smart attendance system that combines face recognition, anti-spoofing, emotion detection, and GPS-based validation to reduce proxy attendance and improve reliability in classroom monitoring. The proposed system follows a two-stage workflow. In the first stage, a student is verified through facial analysis and liveness checking while attempting to mark attendance. In the second stage, the system performs location validation for a predefined duration before confirming the record. A Flask-based web application is used to connect the user interface, AI modules, and SQLite database, while an AI assistant is included to help users access attendance-related information. Experimental testing indicates that the integrated approach provides stronger verification than manual or single-factor methods, although performance still depends on lighting conditions, camera quality, and GPS stability. The work demonstrates a practical B.Tech-level implementation of a secure, real-time, and extensible attendance platform.",
        93: "Attendance records are used by institutions to monitor classroom participation, evaluate discipline, and maintain official academic documentation. Because of this, the marking process should be quick, reliable, and resistant to misuse.",
        94: "In many colleges, attendance is still recorded manually or through limited digital tools. These approaches consume lecture time, depend on human supervision, and rarely prevent proxy attendance in a dependable manner.",
        95: "The proposed project addresses this problem by combining identity verification and physical-presence verification in the same workflow. Face recognition is used for student identification, anti-spoofing helps reject fake inputs such as printed photos or screen replays, and GPS-based validation checks whether the student remains within the approved location.",
        96: "A lightweight AI assistant is also integrated into the system so that users can view attendance information and obtain simple responses through an interactive interface. This feature improves usability without changing the core attendance logic.",
        97: "By integrating these modules in a web-based architecture, the system aims to reduce manual effort, strengthen authenticity, and provide a more practical solution for smart attendance management.",
        99: "Many institutions still use attendance procedures that are slow to execute and weak in verification. As a result, teachers spend valuable class time on routine record keeping, while the system remains vulnerable to false or incomplete attendance entries.",
        100: "The major issues identified in the existing process are:",
        101: "Manual marking increases classroom overhead and interrupts teaching time",
        102: "Proxy attendance is difficult to detect in paper-based or loosely monitored systems",
        103: "Presence is often recorded without verifying whether the student is physically at the required location",
        104: "Most low-cost solutions do not provide real-time validation after attendance is marked",
        105: "Single-factor systems do not offer enough protection against misuse or false confirmation",
        106: "These issues justify the need for an automated attendance platform that verifies both identity and location before finalizing the record.",
        108: "The primary aim of this project is to design and implement a smart attendance platform that improves security, reduces manual work, and remains practical for classroom use.",
        109: "The main objectives of the project are:",
        110: "To automate attendance marking so that classroom time is used more efficiently",
        111: "To identify students through face recognition instead of manual roll calls",
        112: "To reduce spoofing attempts by adding a liveness or anti-spoofing check",
        113: "To validate the user's classroom presence through GPS-based location monitoring",
        114: "To confirm attendance only after a post-marking verification interval",
        115: "To provide separate interfaces for administrators and students",
        116: "To include an AI assistant for basic attendance queries and data access",
        117: "To improve transparency and traceability in attendance records",
        118: "To keep the architecture extensible for future deployment at a larger scale",
        119: "Overall, the objective is to build a more secure and dependable attendance workflow than traditional manual or single-module methods.",
        121: "The scope of this project is limited to the design and implementation of a working prototype that can operate in a classroom-like environment using commonly available hardware and software.",
        122: "The present work covers the following functional boundaries:",
        123: "Attendance marking through a webcam-enabled web interface",
        124: "Real-time face capture and identity matching for registered users",
        125: "Location verification within an admin-defined range during the attendance session",
        126: "A two-stage workflow in which attendance remains provisional until verification is completed",
        127: "Administrative control over session creation, timing, and attendance review",
        128: "Student-side access to attendance status and attendance history",
        129: "Integration of AI modules and location logic in a single application workflow",
        130: "Prototype-level deployment for a limited number of users",
        131: "Potential adaptation of the same framework for offices or training environments",
        132: "The project does not attempt full-scale institutional deployment, but it establishes a solid base for future expansion and refinement.",
    }
)

REPLACEMENTS.update(
    {
        366: "This chapter discusses the observed behavior of the implemented system during testing and evaluates how well the integrated modules performed in practice.",
        367: "Testing was carried out in multiple scenarios so that both individual modules and the complete attendance workflow could be examined under realistic operating conditions.",
        369: "The evaluation strategy was incremental: each feature was tested separately first, and the full system was tested after integration.",
        371: "The face recognition module was evaluated using registered student images captured through the attendance interface.",
        372: "Registered users were identified correctly in most normal test cases",
        373: "Accuracy improved when lighting remained stable",
        374: "Recognition delays increased in low-light situations",
        375: "Camera position and face visibility influenced performance",
        377: "Spoofing resistance was tested using printed photographs and images shown on a mobile screen.",
        378: "Most fake inputs were detected and rejected successfully",
        379: "Live-user detection improved when the subject showed slight natural movement",
        380: "Static image attacks were rejected more reliably than dynamic replay attempts",
        381: "These results indicate that liveness checking adds a meaningful security layer to the system.",
        383: "Emotion detection was tested using a small set of basic facial expressions.",
        384: "The module detected common expressions such as happy, neutral, and sad",
        385: "Outputs varied slightly with lighting and viewing angle",
        386: "Performance was better in stable capture conditions",
        387: "Although this module is not the main basis of attendance, it demonstrates the extensibility of the AI pipeline.",
        389: "GPS behavior was tested under both stationary and movement-based conditions.",
        390: "Distance remained near zero when the user stayed in the same place",
        391: "Location values changed dynamically as the user moved",
        392: "Minor fluctuations were observed because of normal GPS variance",
        393: "Network condition and device quality influenced tracking consistency",
        395: "The full attendance workflow was tested in end-to-end classroom-like scenarios.",
        396: "Students were able to submit attendance within the configured time window",
        397: "GPS tracking started automatically after the initial marking phase",
        398: "Attendance remained provisional during the tracking interval",
        399: "Final confirmation occurred only after successful validation",
        400: "The integrated workflow therefore verified both identity and location before storing the final result.",
        406: "Each module contributed differently to overall system performance. Face recognition handled identity estimation effectively in favorable lighting, anti-spoofing reduced the risk of false acceptance, and GPS validation added a second layer of presence checking.",
        407: "When used together, these modules produced a more trustworthy attendance decision than any single module operating in isolation.",
        409: "The combined system showed good practical accuracy for prototype-level deployment. Because attendance depended on recognition, liveness, and location checks, the chance of false confirmation was lower than in manual or single-factor methods.",
        410: "Reliability was highest when camera quality, lighting, and network stability were within normal operating limits. Under these conditions, the workflow remained consistent and responsive.",
        412: "The main limitations observed during testing were environmental sensitivity and resource dependence. Indoor GPS fluctuation and weak lighting sometimes affected module quality, and concurrent processing occasionally increased response time on moderate hardware.",
        413: "These limitations do not invalidate the prototype, but they indicate the areas that require optimization before larger deployment.",
        415: "Overall, the proposed system can be considered effective for secure attendance prototyping. It reduces the scope for proxy attendance and demonstrates the advantage of combining multimodal verification in a classroom context.",
        416: "With further optimization, the same framework can be extended into a more scalable solution for institutional use.",
        439: "This chapter summarizes the contribution of the project and highlights the most important directions for future improvement.",
        441: "The work presented in this report demonstrates a multimodal smart attendance system that combines face recognition, anti-spoofing, emotion detection, and GPS-based verification within a single web platform.",
        442: "Instead of relying on one input alone, the system validates attendance through multiple checkpoints. This design improves confidence in the final record and reduces the possibility of proxy marking.",
        443: "The staged attendance workflow, in which marking is followed by location validation, is one of the key contributions of the project. It ensures that attendance is not confirmed only on the basis of a momentary login or image capture.",
        444: "Testing showed that the prototype performs well under normal classroom conditions, especially when lighting, camera quality, and network availability are reasonably stable. Some GPS variation and response delays were observed, but the system remained functionally reliable.",
        445: "The inclusion of an AI assistant further improves usability by allowing users to access attendance information in a more interactive manner.",
        446: "In summary, the project meets its objective of developing a practical and more secure alternative to traditional attendance methods.",
        449: "The limitations observed in this work are mainly related to real-world sensing conditions and prototype-scale deployment.",
        450: "Face recognition accuracy decreases when the lighting is weak or the camera feed is unclear.",
        451: "GPS readings may fluctuate indoors or in areas where device and network conditions are unstable.",
        452: "The system depends on webcam access, browser permissions, and internet connectivity for full functionality.",
        453: "Running several AI and tracking operations simultaneously can introduce response delays on mid-range hardware.",
        454: "The present prototype has been evaluated for a limited number of users and may require optimization before large-scale deployment.",
        455: "Emotion analysis is included as an auxiliary feature and is not yet robust enough to serve as a primary attendance signal.",
        456: "These limitations are acceptable at prototype level, but they should be addressed before production deployment.",
        458: "Several extensions can make the system more practical and scalable in future work.",
        459: "A dedicated mobile application could improve accessibility and make attendance interactions more convenient for end users.",
        460: "Cloud-based storage and deployment can improve scalability, synchronization, and centralized record management.",
        461: "More advanced recognition and liveness models can be integrated to improve robustness across varied environmental conditions.",
        462: "Location verification can be refined through better filtering or additional sensing techniques to reduce GPS fluctuation.",
        463: "Notification features can be added for session reminders, attendance alerts, and validation outcomes.",
        464: "The AI assistant can be extended to generate richer analytics and early-warning insights for low attendance.",
        465: "Integration with institutional ERP or academic management systems would improve practical usefulness.",
        466: "Additional security mechanisms such as multi-factor authentication can further strengthen access control and record integrity.",
        467: "These enhancements would move the project closer to a production-ready attendance platform for wider deployment.",
        478: "Journal Articles:",
        482: "Books:",
        483: "I. Goodfellow, Y. Bengio, and A. Courville, Deep Learning, 1st ed. Cambridge: MIT Press, 2016.",
        485: "Web Resources:",
    }
)

REPLACEMENTS.update(
    {
        228: "The system follows a layered design in which each module handles a specific responsibility. This separation improves maintainability and reduces coupling between the user interface, backend logic, AI pipeline, and data layer.",
        230: "The main layers of the proposed architecture are:",
        231: "User interface layer for admin and student interactions",
        232: "Application logic layer for authentication, workflow control, and validation rules",
        233: "AI processing layer for recognition, spoof detection, and emotion analysis",
        234: "Location monitoring layer for GPS capture and distance evaluation",
        235: "Database layer for persistent storage of users, sessions, and attendance records",
        236: "Communication between these layers allows attendance decisions to be made from combined evidence rather than from a single input.",
        238: "The admin module controls the operational parameters of the system and supervises attendance activity.",
        239: "The admin module is responsible for the following tasks:",
        240: "Creating and scheduling attendance sessions",
        241: "Setting classroom coordinates and the allowed distance threshold",
        242: "Defining attendance windows and post-marking tracking duration",
        243: "Reviewing attendance status and student activity",
        244: "Accessing reports and summary analytics",
        245: "This design keeps configuration authority with the administrator while allowing the process to run automatically once a session starts.",
        247: "The student module provides the user-facing workflow through which attendance is marked and reviewed.",
        248: "The student module includes the following functions:",
        249: "Secure login using registered credentials",
        250: "Face capture through the webcam interface",
        251: "Viewing current attendance status",
        252: "Checking previous attendance records",
        253: "Interacting with the built-in AI assistant",
        254: "The student interface was kept simple so that the verification process remains understandable even for first-time users.",
        256: "The end-to-end attendance workflow is summarized below:",
        257: "Admin creates a session for a specific class or subject",
        258: "Student logs into the system during the permitted time window",
        259: "Student submits face and location data through the browser",
        260: "System performs recognition and liveness verification",
        261: "Attendance is stored as provisional rather than final",
        262: "Post-marking GPS tracking begins after the initial attendance phase",
        263: "Location is monitored for the configured duration",
        264: "Attendance is confirmed or cancelled on the basis of validation outcome",
        265: "This sequence strengthens attendance authenticity by checking both identity and continued presence.",
        269: "This chapter explains how the proposed design was implemented as an operational prototype. The implementation combines frontend interfaces, backend services, AI modules, and database support in a coordinated workflow.",
        270: "During development, priority was given to stability, modularity, and understandable system behavior so that the application could be demonstrated reliably under normal classroom conditions.",
        272: "The detailed design follows a modular structure in which each component performs a limited role and exchanges data through defined application logic.",
        273: "The main implementation components are:",
        274: "Frontend interfaces for admin and student users",
        275: "A Flask-based backend server",
        276: "AI modules for recognition, anti-spoofing, and emotion detection",
        277: "A GPS-based validation layer",
        278: "A database for record storage and retrieval",
        279: "This structure allows data to move from user input to decision logic in a clear and traceable manner.",
        281: "The user interface was designed to support quick attendance marking and simple administrative control. HTML, CSS, and JavaScript were used to keep the interface lightweight and browser accessible.",
        282: "The interface is divided into two primary views:",
        283: "Admin interface",
        284: "Session creation and management controls",
        285: "Location, radius, and timing configuration",
        286: "Attendance reports and summary analytics",
        287: "Student interface",
        288: "Login and attendance access dashboard",
        289: "Webcam-based face capture interface",
        290: "Real-time feedback for attendance and tracking status",
        291: "The user interface emphasizes clarity so that users can understand each step of the attendance process without additional training.",
        293: "The backend was implemented in Flask and acts as the coordination layer for authentication, attendance rules, database communication, and AI module invocation.",
        294: "Its main responsibilities include:",
        295: "Handling login and session validation",
        296: "Managing attendance-session lifecycle events",
        297: "Processing requests received from the frontend",
        298: "Invoking AI models and location checks",
        299: "Writing and reading records from the database",
        300: "The backend logic was organized to support multiple users while maintaining a predictable attendance workflow.",
        302: "Artificial intelligence is central to the proposed system because the final attendance decision depends on more than one verification signal.",
        303: "Face Recognition Model",
        304: "The face recognition module compares the captured facial image with registered student data to estimate identity consistency.",
        305: "Anti-Spoofing Model",
        306: "The anti-spoofing module helps distinguish a live subject from printed, replayed, or screen-based fraudulent inputs.",
        307: "Emotion Detection Model",
        308: "The emotion-detection module adds contextual facial analysis and demonstrates how auxiliary AI signals can be integrated into the same platform.",
        309: "Together, these models provide a stronger verification pipeline than a single-model attendance system.",
        311: "The GPS module is used to estimate whether the student remains within the authorized location during the verification period.",
        312: "Its working principle can be summarized as follows:",
        313: "The browser collects user location after permission is granted",
        314: "The admin defines the reference location and acceptable radius",
        315: "The system computes distance between the reported and approved coordinates",
        316: "Attendance remains valid only when the user stays within the defined range",
        317: "Continuing location checks after initial marking makes it harder to record attendance and then immediately leave the class area.",
        319: "The database layer stores the persistent information required for attendance execution and later review. SQLite was selected because it is simple to manage in a prototype environment.",
        320: "The database maintains records for:",
        321: "Student profiles",
        322: "Attendance entries",
        323: "Session schedules and timing data",
        324: "GPS and validation-related logs",
        325: "A compact schema was preferred so that core operations remain easy to test, query, and maintain.",
        328: "The system produces multiple output types during normal operation, ranging from live validation feedback to summary reporting.",
        330: "During attendance marking, the interface displays the following real-time information:",
        331: "Live webcam feed",
        332: "Recognition and liveness-verification results",
        333: "Emotion-analysis output",
        334: "Current GPS coordinates and distance status",
        335: "These outputs allow the user to understand the verification process while it is happening.",
        337: "The application presents different attendance states based on verification progress:",
        338: "Not Marked: no attendance attempt has been completed",
        339: "Marked but Not Finalized: initial verification passed and GPS tracking is still active",
        340: "Successfully Recorded: attendance was confirmed after validation",
        341: "Cancelled: attendance was rejected because the required conditions were not met",
        342: "Clear state messages improve transparency for both students and administrators.",
        344: "The system also generates analytical outputs that help users review participation patterns:",
        345: "Total classes attended",
        346: "Subject-wise attendance summary",
        347: "Attendance percentage",
        348: "Weekly or monthly attendance trends",
        349: "These outputs support monitoring and basic decision-making at both the student and admin levels.",
    }
)

REPLACEMENTS.update(
    {
        134: "This chapter reviews the major attendance approaches reported in practice and in prior academic work. The review is used to identify the strengths of existing systems and the limitations that motivate the proposed multimodal design.",
        135: "Earlier attendance solutions have relied on manual registers, RFID cards, biometric devices, mobile applications, and computer-vision-based systems. While each approach offers certain benefits, most of them face trade-offs in security, convenience, cost, or verification depth.",
        137: "The main categories of existing attendance systems are summarized below.",
        139: "Manual attendance depends on paper registers or verbal roll calls, making it easy to deploy but inefficient in large classrooms [Add citation on manual attendance limitations].",
        140: "Simple to understand and implement",
        141: "Consumes lecture time as class size increases",
        142: "Susceptible to human error during record entry",
        143: "Provides no built-in safeguard against proxy attendance",
        144: "For modern classrooms, manual methods are easy to start with but weak in speed, auditability, and security.",
        146: "RFID-based attendance systems assign each student a card or tag that is scanned at the point of entry [Add citation on RFID attendance systems].",
        147: "Faster than manual register-based attendance",
        148: "Relatively low complexity during day-to-day use",
        149: "Cards can be shared or exchanged among students",
        150: "The scan confirms possession of a card, not the true identity of the user",
        151: "RFID improves speed, but its security is limited unless it is combined with stronger identity verification.",
        153: "Biometric attendance systems use fingerprints, iris patterns, or similar physical traits to identify users [Add citation on biometric attendance systems].",
        154: "Offers stronger identity assurance than card-based systems",
        155: "Reduces the chance of intentional proxy attendance",
        156: "Requires dedicated hardware installation and maintenance",
        157: "May be less convenient in crowded or shared environments",
        158: "Biometric systems are effective, but cost, hygiene, and device dependency can limit their adoption.",
        160: "Face-recognition-based attendance systems identify users through image capture and feature matching [Add citation on face-recognition attendance systems].",
        161: "Non-contact and easy to integrate with webcams",
        162: "Suitable for smart classrooms and web-based platforms",
        163: "Can reuse existing camera hardware in many environments",
        164: "Performance may degrade under spoofing attempts or poor lighting",
        165: "Without liveness detection, face recognition alone may not be sufficient for secure attendance confirmation.",
        167: "Online or app-based attendance systems allow students to mark attendance through mobile or web interfaces [Add citation on app-based attendance systems].",
        168: "Convenient and accessible from different devices",
        169: "Easy to deploy without specialized hardware",
        170: "Often lacks dependable identity and location checks",
        171: "Can be misused if attendance is self-reported without validation",
        172: "These systems improve accessibility but are not always suitable where strict verification is required.",
        174: "The literature survey indicates that previous systems solve only part of the attendance problem. The following gaps are especially relevant to the proposed work.",
        176: "RFID-based solutions are fast, but they do not reliably prevent card sharing or proxy marking. This creates a security gap in environments where authenticity is important [Add citation supporting RFID security limitations].",
        178: "Fingerprint and similar biometric systems improve verification, yet they depend on extra hardware, regular maintenance, and physical interaction. These factors reduce flexibility in large or resource-constrained deployments [Add citation supporting biometric deployment constraints].",
        180: "Many face recognition systems focus only on identity matching and do not include strong anti-spoofing checks. As a result, images or replayed media can sometimes bypass the system [Add citation on spoofing risk in face-recognition systems].",
        182: "Identity validation alone is not enough if the system cannot confirm that the student is actually present in the authorized classroom area. The absence of location verification leaves room for misuse [Add citation on the need for location-aware attendance].",
        184: "A major research gap is the lack of systems that combine face recognition, anti-spoofing, and location validation in one attendance workflow. A multimodal design can improve robustness because each module checks a different type of risk [Add citation on multimodal verification].",
        186: "Most reported attendance platforms also provide limited analytical support to end users. Interactive assistance, automatic summaries, and attendance insights are rarely integrated into the same solution [Add citation on intelligent attendance analytics].",
        188: "Another gap lies in user experience. Many existing systems prioritize backend validation but offer limited dashboard quality, poor feedback, or weak usability for students and administrators [Add citation on UX issues in academic systems].",
        200: "The methodology adopted in this project follows a staged implementation strategy. Instead of building a single monolithic system, the work was divided into problem analysis, module development, system integration, and testing.",
        201: "The core design decision was to combine facial verification, anti-spoofing, emotion analysis, and GPS monitoring into one operational pipeline. Each module was first evaluated independently and then connected through the web application.",
        202: "The workflow was designed for real-time use, with attention to response time, verification order, and practical classroom constraints. Attendance is therefore treated as a validated process rather than a single-click event.",
        204: "The hardware and software requirements were selected to keep the prototype practical, affordable, and reproducible on standard student-level computing resources.",
        206: "The prototype uses the following hardware resources:",
        207: "A computer or laptop with sufficient processing capability for browser and backend execution",
        208: "A webcam for real-time face capture during attendance marking",
        209: "A stable internet connection for web access and geolocation services",
        210: "The webcam directly affects detection quality because clear facial input improves recognition, liveness checking, and emotion analysis.",
        211: "Network stability is important because GPS-related communication and client-server interaction depend on timely data exchange.",
        213: "The software stack combines tools for backend development, computer vision, and user interface design.",
        214: "Python Programming Language",
        215: "Python was used as the primary implementation language because of its mature ecosystem for machine learning, image processing, and rapid prototyping.",
        216: "Flask Framework",
        217: "Flask was used to build the server-side workflow, manage requests, connect modules, and support session-level application logic.",
        218: "OpenCV Library",
        219: "OpenCV was used for camera interaction, image capture, and core computer-vision operations.",
        220: "AI Models",
        221: "Separate AI models were integrated for face recognition, anti-spoofing, and emotion detection so that the system could verify attendance from multiple perspectives.",
        222: "SQLite Database",
        223: "SQLite was used to store student data, attendance logs, and session-level records without requiring a complex database setup.",
        224: "Frontend Technologies (HTML, CSS, JavaScript)",
        225: "These technologies were used to build the admin and student interfaces and to present real-time status information in the browser.",
        226: "This stack was selected because it supports a complete prototype with moderate hardware requirements and straightforward deployment.",
    }
)


def main():
    if not SOURCE_DOC.exists():
        raise FileNotFoundError(SOURCE_DOC)

    temp_dir = Path(tempfile.mkdtemp(prefix="docx_revise_"))
    try:
        with zipfile.ZipFile(SOURCE_DOC, "r") as zin:
            zin.extractall(temp_dir)

        document_xml = temp_dir / "word" / "document.xml"
        settings_xml = temp_dir / "word" / "settings.xml"

        doc_tree = ET.parse(document_xml)
        doc_root = doc_tree.getroot()
        body = doc_root.find("w:body", NS)
        paragraphs = body.findall("w:p", NS)

        for idx, new_text in REPLACEMENTS.items():
            if 1 <= idx <= len(paragraphs):
                replace_paragraph_text(paragraphs[idx - 1], new_text)

        for instr in doc_root.findall(".//w:instrText", NS):
            if instr.text and "TOC" in instr.text:
                instr.text = ' TOC \\o "1-2" \\h \\z \\u '

        settings_tree = ET.parse(settings_xml)
        settings_root = settings_tree.getroot()
        update_fields = settings_root.find("w:updateFields", NS)
        if update_fields is None:
            update_fields = ET.SubElement(settings_root, qn("w:updateFields"))
        update_fields.set(qn("w:val"), "true")

        doc_tree.write(document_xml, encoding="UTF-8", xml_declaration=True)
        settings_tree.write(settings_xml, encoding="UTF-8", xml_declaration=True)

        if OUTPUT_DOC.exists():
            OUTPUT_DOC.unlink()

        with zipfile.ZipFile(OUTPUT_DOC, "w", zipfile.ZIP_DEFLATED) as zout:
            for folder, _, files in os.walk(temp_dir):
                for file_name in files:
                    file_path = Path(folder) / file_name
                    arcname = file_path.relative_to(temp_dir).as_posix()
                    zout.write(file_path, arcname)

        print(f"Created revised report: {OUTPUT_DOC}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
