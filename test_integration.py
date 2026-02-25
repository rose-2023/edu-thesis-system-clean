#!/usr/bin/env python3
"""
集成测试脚本 - 验证 TeacherDashboard, TeacherT5AgentLog, TeacherSubtitles 的 API 端点
确保所有三个主要页面的后端 API 正确实现
"""

import requests
import json
from datetime import datetime

BASE_URL = "http://127.0.0.1:5000"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_test(title):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}[TEST] {title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.RESET}")

def print_success(msg):
    print(f"{Colors.GREEN}✅ {msg}{Colors.RESET}")

def print_error(msg):
    print(f"{Colors.RED}❌ {msg}{Colors.RESET}")

def print_warning(msg):
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.RESET}")

def print_info(msg):
    print(f"{Colors.BLUE}ℹ️  {msg}{Colors.RESET}")

def test_endpoint(method, path, **kwargs):
    """Test an endpoint and return response"""
    url = f"{BASE_URL}{path}"
    try:
        if method == "GET":
            res = requests.get(url, **kwargs)
        elif method == "POST":
            res = requests.post(url, **kwargs)
        elif method == "PATCH":
            res = requests.patch(url, **kwargs)
        else:
            print_error(f"Unknown method: {method}")
            return None
        
        return res
    except Exception as e:
        print_error(f"Request failed: {str(e)}")
        return None

# =============================================================================
# 1. TEACHER DASHBOARD API 测试
# =============================================================================
def test_teacher_dashboard():
    print_test("TeacherDashboard API - /api/teacher_dashboard")
    
    # Test GET /api/teacher_dashboard (main dashboard)
    print_info("Testing GET /api/teacher_dashboard (main dashboard)")
    res = test_endpoint("GET", "/api/teacher_dashboard?range=week")
    if res and res.status_code == 200:
        data = res.json()
        print_success(f"Status: {res.status_code}")
        if data.get("ok"):
            print_success("Response is valid (ok=True)")
            print_info(f"  - Teacher: {data.get('teacher', {}).get('name')}")
            print_info(f"  - Weekly Sessions: {data.get('overview', {}).get('weekly_sessions')}")
            print_info(f"  - Average Accuracy: {data.get('overview', {}).get('avg_accuracy')}%")
            units = data.get('units', [])
            print_info(f"  - Number of Units: {len(units)}")
            if units:
                print_info(f"  - First Unit: {units[0].get('unit')} ({units[0].get('videos_count')} videos)")
        else:
            print_error(f"Response ok=False: {data.get('error')}")
    else:
        print_error(f"Failed with status {res.status_code if res else 'N/A'}")
    
    # Test GET /api/teacher_dashboard/summary
    print_info("\nTesting GET /api/teacher_dashboard/summary")
    res = test_endpoint("GET", "/api/teacher_dashboard/summary")
    if res and res.status_code == 200:
        data = res.json()
        if data.get("ok"):
            print_success(f"Status: {res.status_code} - Response valid")
            videos = data.get('videos', {})
            print_info(f"  - Total Videos: {videos.get('total')}")
            print_info(f"  - Recent Events: {len(data.get('recent_events', []))}")
        else:
            print_error(f"Response ok=False")
    else:
        print_error(f"Failed with status {res.status_code if res else 'N/A'}")
    
    # Test POST /api/teacher_dashboard/units (create unit)
    print_info("\nTesting POST /api/teacher_dashboard/units (create unit - OPTIONAL)")
    test_data = {
        "unit": "TEST_UNIT_" + datetime.now().strftime("%H%M%S"),
        "title": "Test Unit",
        "description": "This is a test unit"
    }
    res = test_endpoint("POST", "/api/teacher_dashboard/units", 
                       json=test_data, 
                       headers={"Content-Type": "application/json"})
    if res and res.status_code in [200, 201, 400]:
        data = res.json()
        if data.get("ok"):
            print_success(f"Created unit: {data.get('unit')}")
        else:
            print_warning(f"Could not create unit (expected if unit exists): {data.get('message')}")
    else:
        print_warning(f"Create unit test skipped (status: {res.status_code if res else 'N/A'})")

# =============================================================================
# 2. TEACHER T5 AGENT LOG API 测试
# =============================================================================
def test_teacher_t5_agent():
    print_test("TeacherT5AgentLog API - /api/teacher/t5")
    
    # Test GET /api/teacher/t5/units
    print_info("Testing GET /api/teacher/t5/units")
    res = test_endpoint("GET", "/api/teacher/t5/units")
    if res and res.status_code == 200:
        data = res.json()
        print_success(f"Status: {res.status_code}")
        items = data.get('items', [])
        print_info(f"  - Number of units: {len(items)}")
        if items:
            first_unit = items[0]
            print_info(f"  - First unit: {first_unit}")
            
            # Test cascading API: GET /api/teacher/t5/videos?unit_id=...
            print_info(f"\nTesting GET /api/teacher/t5/videos with first unit")
            res_videos = test_endpoint("GET", f"/api/teacher/t5/videos", 
                                     params={"unit_id": first_unit})
            if res_videos and res_videos.status_code == 200:
                videos_data = res_videos.json()
                print_success(f"Status: {res_videos.status_code}")
                videos = videos_data.get('items', [])
                print_info(f"  - Number of videos: {len(videos)}")
                
                if videos:
                    first_video = videos[0]
                    video_id = first_video.get('_id') or first_video.get('id')
                    print_info(f"  - First video: {video_id}")
                    
                    # Test cascading API: GET /api/teacher/t5/video_info?video_id=...
                    print_info(f"\nTesting GET /api/teacher/t5/video_info with first video")
                    res_info = test_endpoint("GET", f"/api/teacher/t5/video_info",
                                            params={"video_id": video_id})
                    if res_info and res_info.status_code == 200:
                        info_data = res_info.json()
                        print_success(f"Status: {res_info.status_code}")
                        print_info(f"  - Video info retrieved")
                        
                        # Test GET /api/teacher/t5/generation_status
                        print_info(f"\nTesting GET /api/teacher/t5/generation_status")
                        res_gen = test_endpoint("GET", f"/api/teacher/t5/generation_status",
                                               params={"video_id": video_id, "level": "L1"})
                        if res_gen and res_gen.status_code == 200:
                            gen_data = res_gen.json()
                            print_success(f"Status: {res_gen.status_code}")
                            print_info(f"  - Generation status: {gen_data.get('status', 'N/A')}")
                        else:
                            print_warning(f"Generation status failed: {res_gen.status_code if res_gen else 'N/A'}")
                    else:
                        print_warning(f"Video info failed: {res_info.status_code if res_info else 'N/A'}")
            else:
                print_warning(f"Videos request failed: {res_videos.status_code if res_videos else 'N/A'}")
    else:
        print_error(f"Failed with status {res.status_code if res else 'N/A'}")

# =============================================================================
# 3. TEACHER SUBTITLES API 测试
# =============================================================================
def test_teacher_subtitles():
    print_test("TeacherSubtitles API - /api/subtitle")
    
    # Test GET /api/subtitle/units
    print_info("Testing GET /api/subtitle/units")
    res = test_endpoint("GET", "/api/subtitle/units")
    if res and res.status_code == 200:
        data = res.json()
        print_success(f"Status: {res.status_code}")
        units = data.get('units', [])
        print_info(f"  - Number of units: {len(units)}")
        
        if units:
            first_unit = units[0]
            print_info(f"  - First unit: {first_unit}")
            
            # Test cascading API: GET /api/subtitle/videos?unit=...
            print_info(f"\nTesting GET /api/subtitle/videos with first unit")
            res_videos = test_endpoint("GET", f"/api/subtitle/videos", 
                                      params={"unit": first_unit})
            if res_videos and res_videos.status_code == 200:
                videos_data = res_videos.json()
                print_success(f"Status: {res_videos.status_code}")
                videos = videos_data.get('videos', [])
                print_info(f"  - Number of videos: {len(videos)}")
                
                if videos:
                    first_video = videos[0]
                    video_id = first_video.get('_id')
                    print_info(f"  - First video: {video_id}")
                    
                    # Test cascading API: GET /api/subtitle/versions?video_id=...
                    print_info(f"\nTesting GET /api/subtitle/versions with first video")
                    res_versions = test_endpoint("GET", f"/api/subtitle/versions",
                                                params={"video_id": video_id})
                    if res_versions and res_versions.status_code == 200:
                        versions_data = res_versions.json()
                        print_success(f"Status: {res_versions.status_code}")
                        versions = versions_data.get('versions', [])
                        print_info(f"  - Number of versions: {len(versions)}")
                        
                        # Test GET /api/subtitle/content?video_id=...
                        print_info(f"\nTesting GET /api/subtitle/content")
                        res_content = test_endpoint("GET", f"/api/subtitle/content",
                                                   params={"video_id": video_id})
                        if res_content and res_content.status_code == 200:
                            content_data = res_content.json()
                            if content_data.get('ok'):
                                print_success(f"Status: {res_content.status_code}")
                                print_info(f"  - Subtitle version: {content_data.get('version')}")
                                text_len = len(content_data.get('text', ''))
                                print_info(f"  - Text length: {text_len} characters")
                            else:
                                print_warning(f"Subtitle not found: {content_data.get('message')}")
                        else:
                            print_warning(f"Content request failed: {res_content.status_code if res_content else 'N/A'}")
                    else:
                        print_warning(f"Versions request failed: {res_versions.status_code if res_versions else 'N/A'}")
            else:
                print_warning(f"Videos request failed: {res_videos.status_code if res_videos else 'N/A'}")
    else:
        print_error(f"Failed with status {res.status_code if res else 'N/A'}")

# =============================================================================
# MAIN
# =============================================================================
def main():
    print(f"\n{Colors.BOLD}{Colors.BLUE}")
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║  論文教育系統 - API 集成测试                              ║")
    print("║  Testing: TeacherDashboard, TeacherT5AgentLog, TeacherSubtitles    ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print(f"{Colors.RESET}")
    
    print_info(f"Backend URL: {BASE_URL}")
    print_info("Verifying Flask server is running...")
    
    # Quick health check
    try:
        res = requests.get(f"{BASE_URL}/api/admin/videos", timeout=2)
        print_success("Flask server is running!")
    except Exception as e:
        print_error(f"Flask server appears to be offline: {e}")
        print_warning("Please start the Flask server first with: python run.py")
        return
    
    # Run tests
    test_teacher_dashboard()
    test_teacher_t5_agent()
    test_teacher_subtitles()
    
    print(f"\n{Colors.BOLD}{Colors.BLUE}")
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║  ✅ All API tests completed                              ║")
    print("║  Ready to test in browser: http://localhost:5173         ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print(f"{Colors.RESET}\n")

if __name__ == "__main__":
    main()
