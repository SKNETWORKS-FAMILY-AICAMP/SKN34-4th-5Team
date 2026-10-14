export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const key = process.env["NEXT_PUBLIC_KAKAO_MAP_KEY"]?.trim();
  if (!key) {
    return Response.json(
      { error: "카카오 지도 설정이 필요합니다." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  return Response.json(
    { kakaoMapKey: key },
    { headers: { "Cache-Control": "no-store" } },
  );
}
