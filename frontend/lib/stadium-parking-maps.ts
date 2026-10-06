export type StadiumParkingMapKind = "entrance" | "preferred-area" | "nearby-alternatives" | "access-gates";

export type StadiumParkingMap = {
  stadiumCode: string;
  stadiumName: string;
  src: string;
  width: number;
  height: number;
  kind: StadiumParkingMapKind;
  title: string;
  summary: string;
  visualNotes: readonly string[];
  sourcePageUrl: string;
  credit: string;
  capturedAt: string;
};

/**
 * Parking information derived from the reference material supplied by the user.
 *
 * These images are visual guides rather than geospatial coordinates. When a
 * feature needs a navigable parking pin, resolve and verify that coordinate separately.
 */
export const stadiumParkingMaps = {
  JAMSIL: {
    stadiumCode: "JAMSIL",
    stadiumName: "잠실야구장",
    src: "/images/stadiums/parking-maps/jamsil-parking.png",
    width: 839,
    height: 857,
    kind: "preferred-area",
    title: "잠실종합운동장 P2 주차 구역",
    summary: "종합운동장역 북쪽의 P2 주차 구역과 진입 방향을 안내합니다.",
    visualNotes: ["잠실야구장 동남쪽", "종합운동장역 북쪽", "이미지상 P2 구역 강조"],
    sourcePageUrl: "https://myseatcheck.com/%ec%84%9c%ec%9a%b8-%ec%9e%a0%ec%8b%a4%ec%95%bc%ea%b5%ac%ec%9e%a5-%ec%a3%bc%ec%b0%a8%ec%9e%a5/?swcfpc=1",
    credit: "자리어때(myseatcheck.com)",
    capturedAt: "2026-09-10",
  },
  GOCHEOK: {
    stadiumCode: "GOCHEOK",
    stadiumName: "고척스카이돔",
    src: "/images/stadiums/parking-maps/gocheok-parking.png",
    width: 1453,
    height: 1000,
    kind: "nearby-alternatives",
    title: "고척스카이돔 인근 주차장",
    summary: "돔 내부 주차가 어려워 주변 대체 주차장을 이용해야 합니다.",
    visualNotes: ["구로기계공구상가", "동양미래대학교", "중앙유통단지", "아이파크몰 고척점 등 인근 대안 표시"],
    sourcePageUrl: "https://myseatcheck.com/%EA%B3%A0%EC%B2%99%EC%8A%A4%EC%B9%B4%EC%9D%B4%EB%8F%94-%EC%A3%BC%EC%B0%A8/?swcfpc=1",
    credit: "자리어때(myseatcheck.com)",
    capturedAt: "2026-09-10",
  },
  MUNHAK: {
    stadiumCode: "MUNHAK",
    stadiumName: "인천 SSG 랜더스필드",
    src: "/images/stadiums/parking-maps/incheon-parking.png",
    width: 1083,
    height: 785,
    kind: "preferred-area",
    title: "문학경기장 4주차장",
    summary: "랜더스필드 남쪽의 문학경기장 4주차장과 남동쪽 진입 방향을 안내합니다.",
    visualNotes: ["랜더스필드 남쪽", "제2경인고속도로 북쪽", "이미지상 4주차장과 진입 방향 강조"],
    sourcePageUrl: "https://myseatcheck.com/%ec%9d%b8%ec%b2%9c-%eb%9e%9c%eb%8d%94%ec%8a%a4%ed%95%84%eb%93%9c-%ec%a3%bc%ec%b0%a8%ec%9e%a5-4%ec%a3%bc%ec%b0%a8%ec%9e%a5/?swcfpc=1",
    credit: "자리어때(myseatcheck.com)",
    capturedAt: "2026-09-10",
  },
  SUWON: {
    stadiumCode: "SUWON",
    stadiumName: "수원 KT 위즈 파크",
    src: "/images/stadiums/parking-maps/suwon-parking.png",
    width: 1126,
    height: 880,
    kind: "access-gates",
    title: "수원종합운동장 주차 출입구",
    summary: "북문·서문·남문으로 진입할 수 있으며 동문은 관계자 전용입니다.",
    visualNotes: ["북문 진입 위치", "서문 진입 위치", "남문 진입 위치", "동문은 이미지상 관계자 전용"],
    sourcePageUrl: "https://myseatcheck.com/%ec%88%98%ec%9b%90-kt%ec%9c%84%ec%a6%88%ed%8c%8c%ed%81%ac-%ec%a3%bc%ec%b0%a8/?swcfpc=1",
    credit: "자리어때(myseatcheck.com)",
    capturedAt: "2026-09-10",
  },
  DAEJEON: {
    stadiumCode: "DAEJEON",
    stadiumName: "대전 한화생명 볼파크",
    src: "/images/stadiums/parking-maps/daejeon-parking.jpg",
    width: 912,
    height: 684,
    kind: "entrance",
    title: "대전 한화생명 볼파크 인근 주차장 입구",
    summary: "한화생명 볼파크 북서쪽 체육시설 주차 구역과 동쪽 진입 방향을 안내합니다.",
    visualNotes: ["볼파크 북서쪽", "체육시설 사이 주차 구역", "이미지상 동쪽에서 들어오는 입구 강조"],
    sourcePageUrl: "https://myseatcheck.com/%EB%8C%80%EC%A0%84%ED%95%9C%ED%99%94%EC%83%9D%EB%AA%85%EB%B3%BC%ED%8C%8C%ED%81%AC-%EC%A3%BC%EC%B0%A8%EC%9E%A5/",
    credit: "자리어때(myseatcheck.com)",
    capturedAt: "2026-09-10",
  },
  DAEGU: {
    stadiumCode: "DAEGU",
    stadiumName: "대구 삼성 라이온즈 파크",
    src: "/images/stadiums/parking-maps/daegu-parking.png",
    width: 736,
    height: 799,
    kind: "preferred-area",
    title: "전설로 주차장",
    summary: "라이온즈 파크 남서쪽 전설로 주차장과 서편 진입 방향을 안내합니다.",
    visualNotes: ["라이온즈 파크 남서쪽", "전설로 주차장", "이미지상 서편 진입 방향 강조"],
    sourcePageUrl: "https://myseatcheck.com/%eb%8c%80%ea%b5%ac-%eb%9d%bc%ec%9d%b4%ec%98%a8%ec%a6%88%ed%8c%8c%ed%81%ac-%ec%a3%bc%ec%b0%a8%ec%9e%a5-%ec%95%bc%ea%b5%ac%ec%9e%a5-%ec%a3%bc%ec%b0%a8%ec%9e%a5/?swcfpc=1",
    credit: "자리어때(myseatcheck.com)",
    capturedAt: "2026-09-10",
  },
  GWANGJU: {
    stadiumCode: "GWANGJU",
    stadiumName: "광주-KIA 챔피언스 필드",
    src: "/images/stadiums/parking-maps/gwangju-parking.png",
    width: 954,
    height: 770,
    kind: "entrance",
    title: "챔피언스필드 지하 주차장",
    summary: "주경기장 서쪽 지하 주차 구역과 남서쪽 진입 방향을 안내합니다.",
    visualNotes: ["챔피언스필드 서쪽", "무등야구장 동쪽", "이미지상 남서쪽 진입 방향 강조"],
    sourcePageUrl: "https://myseatcheck.com/%ea%b4%91%ec%a3%bc-%ec%b1%94%ed%94%bc%ec%96%b8%ec%8a%a4%ed%95%84%eb%93%9c-%ec%a3%bc%ec%b0%a8%ec%9e%a5/?swcfpc=1",
    credit: "자리어때(myseatcheck.com)",
    capturedAt: "2026-09-10",
  },
  SAJIK: {
    stadiumCode: "SAJIK",
    stadiumName: "사직야구장",
    src: "/images/stadiums/parking-maps/sajik-parking.png",
    width: 738,
    height: 839,
    kind: "entrance",
    title: "사직야구장 주차장 입구",
    summary: "사직야구장 남동쪽 주차 구역과 남쪽 도로에서 들어오는 입구를 안내합니다.",
    visualNotes: ["사직야구장 남동쪽", "부산아시아드 조각광장 주변", "이미지상 남쪽 진입 방향 강조"],
    sourcePageUrl: "https://myseatcheck.com/%eb%b6%80%ec%82%b0-%ec%82%ac%ec%a7%81%ec%95%bc%ea%b5%ac%ec%9e%a5-%ec%a3%bc%ec%b0%a8%ec%9e%a5-%ec%a3%bc%ec%b0%a8%ec%9e%a5-%ec%9e%85%ea%b5%ac/?swcfpc=1",
    credit: "자리어때(myseatcheck.com)",
    capturedAt: "2026-09-10",
  },
  CHANGWON: {
    stadiumCode: "CHANGWON",
    stadiumName: "창원 NC 파크",
    src: "/images/stadiums/parking-maps/changwon-parking.png",
    width: 931,
    height: 700,
    kind: "preferred-area",
    title: "창원 NC 파크 지상 주차장",
    summary: "NC 파크 남쪽의 지정 지상 주차 구역과 남동쪽 진입 방향을 안내합니다.",
    visualNotes: ["NC 파크 남쪽", "마산야구장 북쪽", "이미지상 지정 지상 주차장 강조"],
    sourcePageUrl: "https://myseatcheck.com/%ec%b0%bd%ec%9b%90nc%ed%8c%8c%ed%81%ac-%ec%a3%bc%ec%b0%a8%ec%9e%a5-%ec%a7%80%ec%83%81-%ec%a3%bc%ec%b0%a8%ec%9e%a5/?swcfpc=1",
    credit: "자리어때(myseatcheck.com)",
    capturedAt: "2026-09-10",
  },
} as const satisfies Record<string, StadiumParkingMap>;

export type StadiumParkingCode = keyof typeof stadiumParkingMaps;

export function getStadiumParkingMap(code: string) {
  return stadiumParkingMaps[code as StadiumParkingCode];
}
