FROM openjdk:11-jre-slim
WORKDIR /app
RUN apt-get update && apt-get install -y wget
RUN wget https://github.com/synthetichealth/synthea/releases/download/v3.3.0/synthea-with-dependencies.jar -O synthea.jar
ENTRYPOINT ["java", "-jar", "synthea.jar", "--exporter.baseDirectory", "/output"]