import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client, create_client


load_dotenv()

app = FastAPI(
    title="AI PATH KOREA API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def create_supabase_client() -> Client:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_secret_key = os.environ.get("SUPABASE_SECRET_KEY")

    if not supabase_url or not supabase_secret_key:
        raise RuntimeError("Supabase 환경변수가 설정되지 않았습니다.")

    return create_client(supabase_url, supabase_secret_key)


@app.get("/api/opportunities")
def get_opportunities() -> dict:
    try:
        supabase = create_supabase_client()

        response = (
            supabase.table("opportunities")
            .select(
                """
                id,
                title,
                slug,
                summary,
                organizer,
                target_audience,
                difficulty,
                format,
                region,
                price_type,
                price_text,
                application_deadline_at,
                event_start_at,
                event_end_at,
                official_url,
                status,
                is_featured,
                last_verified_at,
                categories (
                    name,
                    slug
                ),
                sources (
                    name
                ),
                opportunity_tags (
                    tags (
                        name,
                        slug
                    )
                )
                """
            )
            .neq("status", "draft")
            .neq("status", "cancelled")
            .order("is_featured", desc=True)
            .order("application_deadline_at")
            .execute()
        )

        opportunities = response.data or []

        return {
            "count": len(opportunities),
            "opportunities": opportunities,
        }

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    except Exception as error:
        print(f"Supabase query failed: {error}")

        raise HTTPException(
            status_code=500,
            detail="기회 정보를 불러오지 못했습니다.",
        ) from error


@app.get("/api/opportunities/{slug}")
def get_opportunity(slug: str) -> dict:
    try:
        supabase = create_supabase_client()

        response = (
            supabase.table("opportunities")
            .select(
                """
                id,
                title,
                slug,
                summary,
                description,
                organizer,
                target_audience,
                difficulty,
                format,
                region,
                venue,
                price_type,
                price_text,
                application_start_at,
                application_deadline_at,
                event_start_at,
                event_end_at,
                official_url,
                image_url,
                status,
                last_verified_at,
                categories (
                    name,
                    slug
                ),
                sources (
                    name,
                    homepage_url,
                    attribution_text
                ),
                opportunity_tags (
                    tags (
                        name,
                        slug
                    )
                )
                """
            )
            .eq("slug", slug)
            .neq("status", "draft")
            .neq("status", "cancelled")
            .limit(1)
            .execute()
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    except Exception as error:
        print(f"Supabase detail query failed: {error}")

        raise HTTPException(
            status_code=500,
            detail="기회 상세 정보를 불러오지 못했습니다.",
        ) from error

    opportunities = response.data or []

    if not opportunities:
        raise HTTPException(
            status_code=404,
            detail="해당 기회를 찾을 수 없습니다.",
        )

    return {
        "opportunity": opportunities[0],
    }